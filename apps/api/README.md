# API

Application FastAPI chargée de recevoir les fichiers audio et de créer les
jobs de transcription. PostgreSQL reste la source de vérité ; Redis n'est pas
contacté directement par cette application.

## Responsabilités des couches

Chaque composant conserve une responsabilité précise. La direction principale
des appels est `Routes -> Controllers -> Services -> Repositories/Storage`.

- `Routes` associe une méthode et une URL FastAPI à un controller. Une route ne
  porte ni règle métier ni accès aux données.
- `Middlewares` applique les traitements HTTP transversaux avant ou après le
  controller. Un middleware peut interrompre la requête, mais ne remplace pas
  un service métier.
- `Schemas` contient uniquement les contrats HTTP Pydantic de requête et de
  réponse. Les modèles internes et SQLAlchemy n'y vivent pas.
- `Controllers` traduit le protocole HTTP : il reçoit les paramètres validés,
  appelle la prévalidation propre à l'upload, invoque un service, puis construit
  le statut, les en-têtes et le corps de la réponse.
- `Services` porte les use cases et orchestre stockage, inspection média,
  règles FAST/BATCH et transaction PostgreSQL.
- `Validators` filtre les métadonnées HTTP déclarées (`filename`, taille et
  MIME). Cette validation rapide ne prouve jamais l'authenticité du média.
- `Media` encapsule `ffprobe` et transforme le contenu réel en métadonnées
  internes (`AudioMetadata`).
- `Storage` persiste physiquement les octets. Il ne porte aucune règle HTTP ou
  FAST/BATCH.
- `Helpers`, lorsqu'il existe, est réservé aux fonctions pures, triviales et
  génériques. Aucune règle métier ou infrastructure ne doit y être dissimulée.

Les repositories partagés ne valident pas le protocole HTTP et ne font pas de
commit : la transaction appartient au service appelant. Un composant n'est
déplacé dans `transcribe-ai-shared` que lorsqu'il constitue réellement un
contrat partagé ou que plusieurs applications l'utilisent. Le package commun
ne doit pas devenir une application principale cachée derrière des wrappers.

## Création d'une transcription

### `POST /transcriptions`

La requête utilise `multipart/form-data` avec deux champs :

| Champ | Type | Valeurs |
| --- | --- | --- |
| `audio_file` | fichier | WAV, MP3, OGG ou M4A |
| `type` | chaîne | `FAST` ou `BATCH` |

Exemple :

```bash
curl --request POST http://localhost:8000/transcriptions \
  --form 'audio_file=@sample.wav' \
  --form 'type=FAST'
```

Une création réussie retourne `202 Accepted`, avec l'en-tête
`Location: /transcriptions/{job_uuid}` et le corps suivant :

```json
{
  "job_uuid": "6e960dd3-a433-4bc3-89c4-0bd5ac3e68c4",
  "status": "QUEUED"
}
```

Le controller vérifie d'abord la taille, le nom, l'extension et le MIME déclaré.
Ces valeurs viennent du client et ne sont qu'un filtre rapide. Le flux est
ensuite enregistré sans chargement complet en mémoire. `ffprobe` inspecte le
contenu stocké, identifie conteneur, codec et durée, puis le service vérifie la
cohérence du format et applique la limite du type FAST ou BATCH. Il crée enfin,
dans une transaction PostgreSQL, un `TranscriptionJob` avec `status=QUEUED` et
`dispatch_required=true`.

Les couples conteneur/codec acceptés sont volontairement explicites :

| Conteneur | Codecs acceptés |
| --- | --- |
| WAV | PCM signé 16/24/32 bits ou PCM float 32 bits little-endian |
| MP3 | MP3 |
| OGG | Vorbis ou Opus |
| M4A | AAC ou ALAC |

Cette politique est versionnée avec l'application afin qu'un changement de
codec soit validé avec les workers avant d'être exposé en production.

FastAPI reçoit et analyse toutefois le multipart avant l'exécution du service.
La limite applicative protège donc le stockage final et PostgreSQL, mais elle
doit être complétée en production par une limite de corps au niveau du reverse
proxy ou de l'ingress.

L'inspection crée aussi une copie seekable temporaire et un processus ffprobe
par requête. Le nombre de requêtes concurrentes doit donc être borné au niveau
du déploiement (replicas/ingress) en fonction de l'espace temporaire, des PID et
du CPU disponibles. Une limite applicative dédiée pourra être ajoutée lorsque
la stratégie de concurrence de production sera fixée.

Il n'existe plus de table Outbox. Le dispatcher utilise
`dispatch_required=true` pour identifier les jobs à publier dans Redis.

Si la validation audio ou l'insertion PostgreSQL échoue de façon certaine
après le stockage, le fichier est supprimé. Si le serveur perd uniquement
l'acquittement du `COMMIT`, une nouvelle session recherche d'abord le job. En
cas d'incertitude persistante, le fichier est conservé : la maintenance peut
supprimer un orphelin, tandis que supprimer l'audio d'un job effectivement
validé rendrait ce job irrécupérable.

### Erreurs publiques

<!-- markdownlint-disable MD013 -->

| Statut | Situation |
| --- | --- |
| `413 Payload Too Large` | fichier supérieur à la limite configurée |
| `415 Unsupported Media Type` | extension, MIME, conteneur ou codec non pris en charge |
| `422 Unprocessable Content` | métadonnée obligatoire absente, audio invalide/trop long ou `type` invalide |
| `503 Service Unavailable` | stockage, PostgreSQL ou `ffprobe` indisponible |

Les réponses d'erreur ne doivent pas exposer de chemin local, d'URL de base de
données ni la sortie brute de `ffprobe`.

## Configuration

| Variable | Obligatoire | Défaut | Description |
| --- | --- | --- | --- |
| `DATABASE_URL` | oui | - | URL PostgreSQL utilisée pour créer le job |
| `AUDIO_STORAGE_PATH` | non | `tmp/audios_buffers` | Racine du stockage audio partagé |
| `API_MAX_UPLOAD_SIZE_BYTES` | non | `104857600` | Limite stricte de 100 MiB, en octets |
| `FFPROBE_PATH` | non | `ffprobe` | Nom ou chemin du binaire d'inspection |
| `FFPROBE_TIMEOUT_SECONDS` | non | `30` | Délai maximal de l'inspection ffprobe |
| `API_FAST_MAX_DURATION_SECONDS` | non | `900` | Durée maximale FAST (15 minutes) |
| `API_BATCH_MAX_DURATION_SECONDS` | non | `14400` | Durée maximale BATCH (4 heures) |
| `API_HOST` | non | `127.0.0.1` | Interface d'écoute Uvicorn |
| `API_PORT` | non | `8000` | Port d'écoute Uvicorn |

<!-- markdownlint-enable MD013 -->

Le package ne charge pas implicitement `.env`. L'environnement doit être
fourni par le shell, Docker ou l'orchestrateur. Le fichier `.env.example`
documente des valeurs de développement et ne contient aucun secret réel.

## Exécution locale

`ffprobe` doit être présent dans le `PATH` ou désigné par `FFPROBE_PATH`. Sur
Debian, il est fourni par le paquet `ffmpeg`, également installé dans l'image.
Depuis la racine du workspace :

```bash
uv sync
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  AUDIO_STORAGE_PATH=tmp/audios_buffers \
  uv run --package api api
```

## Tests

Les tests unitaires utilisent des doubles pour le stockage, le media probe et
le repository ; la prévalidation HTTP est testée séparément :

```bash
uv run pytest -m unit apps/api/tests
```

Les tests d'intégration nécessitent Docker. Testcontainers démarre PostgreSQL,
Alembic construit le schéma et `tmp_path` isole le stockage local :

```bash
uv run pytest -m integration apps/api/tests
```

## Image Docker

L'image doit être construite depuis la racine du workspace :

```bash
docker build \
  --file apps/api/Dockerfile \
  --tag transcribe-ai-api:local \
  .
```

L'étage de construction installe uniquement les dépendances du package API
à partir de `uv.lock`. L'image d'exécution contient `ffmpeg`, qui fournit
`ffprobe`, et lance Uvicorn avec un utilisateur non privilégié sur le port
`8000`.

```bash
docker run --rm \
  --publish 8000:8000 \
  --env DATABASE_URL \
  --env AUDIO_STORAGE_PATH=/data/transcriptions \
  --volume <audio-volume>:/data/transcriptions \
  transcribe-ai-api:local
```

Le volume doit être partagé avec les workers et l'application maintenance, et
doit autoriser l'utilisateur non privilégié du conteneur à écrire. En
production, `DATABASE_URL` doit provenir du gestionnaire de secrets de
l'orchestrateur.

## Authentification

Cette étape ne définit pas encore de mécanisme d'authentification : la route de
création est publique. Un middleware FastAPI d'authentification devra être
ajouté avant toute exposition sur un réseau non fiable.
