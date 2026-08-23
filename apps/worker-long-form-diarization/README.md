# Worker long-form diarization

Cette application consomme les nouveaux messages et récupère périodiquement les
anciens pending du stream `transcription:long-form-diarization`, un par un.
Chaque message passe par la même décision PostgreSQL avant une éventuelle
délégation au transcriber injecté. La boucle conserve les connexions et le
transcriber entre deux messages. Pendant l'inférence, elle renouvelle le lease
PostgreSQL selon
`WORKER_HEARTBEAT_SECONDS`. Après une inférence réussie, le résultat et l'état
`COMPLETED` sont committés ensemble avant l'ACK Redis. Elle ne contient aucune
dépendance FastAPI ou Flask.

L'adapter de production utilise
[WhisperX 3.8.6](https://github.com/m-bain/whisperX/tree/v3.8.6) pour la
transcription et l'alignement, puis
[`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
pour attribuer les locuteurs. La langue est toujours fixée à `fr`. Le modèle
ASR vaut `large-v3-turbo` par défaut et peut être remplacé par `large-v3`.

Le flux audio fourni par `AudioStorage` est matérialisé dans un fichier
temporaire, puis tout le pipeline ML synchrone est exécuté dans un thread. La
boucle asyncio reste disponible pour renouveler le lease pendant une longue
inférence. Le fichier temporaire est supprimé après le traitement. Une
annulation asyncio empêche la finalisation du job, mais ne peut pas interrompre
immédiatement un calcul natif déjà engagé dans le thread.

Le résultat conserve les timestamps et les locuteurs dans un format indépendant
du moteur :

```json
{
  "text": "Bonjour. Comment allez-vous ? Très bien.",
  "language": "fr",
  "speaker_count": 2,
  "segments": [
    {
      "start": 0.0,
      "end": 2.4,
      "text": "Bonjour. Comment allez-vous ?",
      "speaker": "SPEAKER_00"
    },
    {
      "start": 2.5,
      "end": 3.2,
      "text": "Très bien.",
      "speaker": "SPEAKER_01"
    }
  ]
}
```

Deux segments consécutifs attribués au même locuteur sont fusionnés : le début
du premier, la fin du dernier et les textes concaténés forment un seul message.
Un segment sans locuteur (`speaker = null`) constitue une frontière et n'est
pas fusionné avec ses voisins. `speaker_count` correspond au nombre de labels
non nuls distincts.

Une erreur retryable réarme le job dans PostgreSQL avant de supprimer l'ancien
message Redis. Une erreur permanente, ou une tentative ayant atteint
`MAX_ATTEMPTS`, termine le job en `FAILED` sans nouvelle publication.

Le premier balayage du Pending Entries List a lieu au démarrage, puis toutes les
`WORKER_AUTOCLAIM_INTERVAL_SECONDS`. Seuls les messages inactifs depuis au moins
`WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS` sont réattribués. Ce délai Redis ne
constitue jamais une preuve de crash, même pour une transcription
`LONG_FORM_DIARIZATION` très longue : un job `PROCESSING` pour la tentative
courante reste pending et seul son lease PostgreSQL gouverne sa recovery. Les
jobs terminaux, les anciennes tentatives et les UUID absents sont nettoyés sans
inférence. Un UUID absent est considéré comme obsolète, puisque le contrat de
publication exige que le job PostgreSQL soit committé avant le message Redis.
Le balayage reste séquentiel et ne concurrence pas l'inférence en cours.

## Configuration du moteur

- `WORKER_TRANSCRIBER_BACKEND` vaut `whisperx` par défaut et accepte `fake` en
  développement uniquement ;
- `WORKER_TRANSCRIBER_MODEL` accepte `large-v3-turbo` (défaut) ou `large-v3` ;
- `WORKER_TRANSCRIBER_DEVICE` accepte `cuda` (défaut) ou `cpu` ;
- `WORKER_TRANSCRIBER_COMPUTE_TYPE` vaut `default`. WhisperX choisit ainsi le
  type adapté au device, notamment `float16` sur CUDA et `float32` sur CPU ;
- `WORKER_TRANSCRIBER_BATCH_SIZE` vaut `16` et doit être compris entre 1 et
  256 ;
- `WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN` est obligatoire avec WhisperX. Il
  est représenté comme un secret par Pydantic et ne doit jamais être journalisé
  ni committé ;
- `WORKER_TRANSCRIBER_MODEL_DIRECTORY` vaut `/models` et doit être un chemin
  absolu persistant ;
- `WORKER_TRANSCRIBER_DIARIZATION_MODEL` est fixé à
  `pyannote/speaker-diarization-community-1` ;
- `AUDIO_STORAGE_PATH` doit viser le volume audio partagé avec l'API.

Avant le premier lancement, accepter les conditions d'accès du modèle Pyannote
sur Hugging Face et créer un token disposant du droit de lecture. Les modèles
ASR, d'alignement et de diarisation sont chargés une fois au démarrage, avant la
consommation Redis. Le premier démarrage les télécharge dans les sous-répertoires
persistants de `/models`; les démarrages suivants réutilisent ce cache.

Les dépendances ML sont optionnelles afin que les tests unitaires standards et
l'import du package n'installent ni Torch ni les modèles. Les extras `cpu` et
`gpu` sont incompatibles entre eux et sélectionnent les wheels PyTorch adaptés.
La version `pyannote-audio==4.0.4` du projet historique est conservée. En
revanche, ses versions Torch 2.10 et `huggingface-hub` 1.x ne sont pas
reprises : WhisperX 3.8.6 impose la famille Torch 2.8 et
`huggingface-hub<1`. Le lockfile utilise donc ces contraintes compatibles
plutôt qu'un assemblage qui ne peut pas être résolu.

Pour une exécution CPU locale :

```shell
uv sync --package worker-long-form-diarization --extra cpu

WORKER_ID=worker-long-form-diarization-local \
WORKER_TRANSCRIBER_DEVICE=cpu \
WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN=hf_xxx \
WORKER_TRANSCRIBER_MODEL_DIRECTORY=/chemin/absolu/models \
uv run --package worker-long-form-diarization --extra cpu \
  worker-long-form-diarization
```

## Exécution avec GPU dans Docker

L'image installe l'extra `gpu` basé sur les wheels PyTorch CUDA 12.8, ainsi que
FFmpeg pour le décodage audio. L'hôte doit disposer :

- d'un GPU NVIDIA compatible et de son pilote ;
- de Docker ;
- du
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  configuré pour Docker avec `nvidia-ctk runtime configure --runtime=docker`.

Après redémarrage de Docker, vérifier l'accès au GPU :

```shell
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

Construire l'image depuis la racine du workspace :

```shell
docker build --file apps/worker-long-form-diarization/Dockerfile \
  --tag transcribe-ai-worker-long-form-diarization .
```

Placer le token dans un fichier non versionné, lisible par son propriétaire
uniquement :

```dotenv
WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN=hf_xxx
```

Puis lancer le worker :

```shell
docker run --rm --gpus all \
  --network backend-network \
  --env-file /chemin/worker-long-form-diarization.secrets.env \
  --env DATABASE_URL=postgresql://postgres:postgres@postgres/postgres \
  --env REDIS_URL=redis://redis:6379/0 \
  --env WORKER_ID=worker-long-form-diarization-1 \
  --volume transcribe-audio:/data/transcriptions:ro \
  --volume transcribe-long-form-models:/models \
  transcribe-ai-worker-long-form-diarization
```

`backend-network` doit être remplacé par le réseau Docker où PostgreSQL et
Redis sont résolubles. Avec un bind mount, `/models` doit être inscriptible par
l'UID `10001`; un volume Docker neuf hérite des permissions de l'image. Chaque
replica charge sa propre copie des modèles : le nombre de replicas doit donc
être dimensionné selon la VRAM disponible.

## Fake de développement

Le fake reste disponible pour les tests et les compositions locales. Son
activation exige les deux variables explicites suivantes :

```shell
WORKER_ENVIRONMENT=development
WORKER_TRANSCRIBER_BACKEND=fake
```

Si le fake est demandé en production, le worker échoue clairement sans produire
de faux résultat. Aucun token Hugging Face n'est requis pour ce backend.

Depuis la racine du workspace :

```shell
uv run --package worker-long-form-diarization worker-long-form-diarization
```

Les autres variables sont documentées dans `.env.example`. `WORKER_ID` sert à
la fois d'identifiant de consumer Redis et de propriétaire du lease PostgreSQL ;
il doit donc être unique pour chaque instance concurrente du worker.
`WORKER_HEARTBEAT_SECONDS` doit être strictement inférieur à
`WORKER_LEASE_SECONDS` ; un renouvellement refusé interrompt la tentative sans
finaliser ni acquitter son message.
`MAX_ATTEMPTS` compte la tentative initiale : avec la valeur `3`, les tentatives
portent les indices `0`, `1` et `2`.

## Tests

```shell
uv run pytest -m unit apps/worker-long-form-diarization/tests
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
```

Les tests unitaires injectent des doubles pour WhisperX et Pyannote : ils ne
téléchargent aucun modèle et ne requièrent pas les extras ML. Le test
d'inférence réel porte le marker `gpu`. Il télécharge dans le répertoire
temporaire pytest le dialogue français
[A Formal Conversation](https://commons.wikimedia.org/wiki/File:French_Dialogue_-_A_Formal_Conversation.ogg),
créé par Hagindaz pour le Wikibook French et distribué sous CC BY-SA 3.0 / GFDL
1.2+. Le téléchargement est borné à 1 Mio et protégé par une taille et une
empreinte SHA-256 attendues avant l'inférence
(`f700390a491a1af077d65fb29e0e7099a711324f6c1847308ba9dc74bc40ff1a`). Le
test exige donc un accès réseau, le token Hugging Face et un cache modèle
persistant. Il reste exclu de la CI standard :

```shell
RUN_GPU_TESTS=1 \
WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN=hf_xxx \
WORKER_TRANSCRIBER_MODEL_DIRECTORY=/chemin/absolu/models \
uv run --package worker-long-form-diarization --extra gpu \
  pytest -m gpu apps/worker-long-form-diarization/tests/gpu
```

Le second scénario exerce le runtime commun avec PostgreSQL et Redis réels via
Testcontainers, notamment face à deux messages dupliqués.
