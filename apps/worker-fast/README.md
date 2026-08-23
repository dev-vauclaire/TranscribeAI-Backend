# Worker FAST

Cette application consomme les nouveaux messages et récupère périodiquement les
anciens pending du stream `transcription:fast`, un par un. Chaque message passe
par la même décision PostgreSQL avant une éventuelle délégation au transcriber
injecté. La boucle conserve les connexions et le modèle entre deux messages.
Pendant l'inférence, elle renouvelle le lease PostgreSQL selon
`WORKER_HEARTBEAT_SECONDS`. Après une inférence réussie, le résultat et l'état
`COMPLETED` sont committés ensemble avant l'ACK Redis. Elle ne contient aucune
logique HTTP.

L'adapter de production utilise
[Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) avec
`large-v3-turbo` par défaut. La langue est toujours fixée à `fr` et le filtre
Silero VAD est toujours actif. Il produit un résultat indépendant du moteur :

```json
{
  "text": "Bonjour tout le monde.",
  "language": "fr",
  "segments": [
    {"start": 0.0, "end": 1.25, "text": "Bonjour tout le monde."}
  ]
}
```

L'ouverture du flux `AudioStorage`, l'appel synchrone au modèle et la
consommation complète de son générateur de segments sont déportés dans un
thread. L'event loop reste ainsi disponible pour le heartbeat. Une annulation
asyncio empêche toujours la finalisation du job, mais elle ne peut pas arrêter
immédiatement un calcul natif déjà lancé dans ce thread.

Une erreur retryable réarme le job dans PostgreSQL avant de supprimer l'ancien
message Redis. Une erreur permanente, ou une tentative ayant atteint
`MAX_ATTEMPTS`, termine le job en `FAILED` sans nouvelle publication.

Le premier balayage du Pending Entries List a lieu au démarrage, puis toutes les
`WORKER_AUTOCLAIM_INTERVAL_SECONDS`. Seuls les messages inactifs depuis au moins
`WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS` sont réattribués. Ce délai Redis ne
constitue jamais une preuve de crash : un job `PROCESSING` pour la tentative
courante reste pending et seul son lease PostgreSQL gouverne sa recovery. Les
jobs terminaux, les anciennes tentatives et les UUID absents sont nettoyés sans
inférence. Un UUID absent est considéré comme obsolète, puisque le contrat de
publication exige que le job PostgreSQL soit committé avant le message Redis.
Le balayage reste séquentiel et ne concurrence pas l'inférence en cours.

## Configuration du moteur

- `WORKER_TRANSCRIBER_BACKEND` vaut `faster-whisper` par défaut et accepte
  aussi `fake` ;
- `WORKER_TRANSCRIBER_MODEL` vaut `large-v3-turbo` et accepte un nom Hugging
  Face ou un dossier CTranslate2 local ;
- `WORKER_TRANSCRIBER_DEVICE` vaut `cuda` et accepte aussi `cpu`, sans fallback
  silencieux ;
- `WORKER_TRANSCRIBER_COMPUTE_TYPE` vaut `float16` ;
- `AUDIO_STORAGE_PATH` vaut `tmp/audios_buffers` et cible les fichiers
  `input.*` partagés avec l'API.

Les valeurs CTranslate2 acceptées sont `default`, `auto`, `int8`,
`int8_float32`, `int8_float16`, `int8_bfloat16`, `int16`, `float16`, `float32`
et `bfloat16`. Leur disponibilité effective dépend du matériel. La
[documentation CTranslate2](https://opennmt.net/CTranslate2/quantization.html)
détaille les conversions et accélérations disponibles.

Pour une exécution CPU explicite :

```shell
WORKER_ID=worker-fast-local \
WORKER_TRANSCRIBER_DEVICE=cpu \
WORKER_TRANSCRIBER_COMPUTE_TYPE=int8 \
uv run --package worker-fast worker-fast
```

Le modèle est téléchargé depuis Hugging Face au premier démarrage si sa valeur
n'est pas un dossier local. Le cache doit être persistant en production afin
d'éviter un nouveau téléchargement à chaque déploiement.

## Exécution avec GPU dans Docker

Faster-Whisper 1.2 utilise CTranslate2. Son exécution GPU demande CUDA 12,
cuBLAS CUDA 12 et cuDNN 9. L'image du worker installe ces bibliothèques via
l'extra Python `gpu`; FFmpeg système n'est pas nécessaire, car PyAV embarque
les bibliothèques de décodage.

L'hôte doit disposer :

- d'un GPU NVIDIA compatible et de son pilote ;
- de Docker ;
- du
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  configuré pour Docker avec `nvidia-ctk runtime configure --runtime=docker`.

Après redémarrage de Docker, vérifier l'accès au GPU avec une image CUDA 12 :

```shell
docker run --rm --gpus all nvidia/cuda:12.9.2-base-ubuntu24.04 nvidia-smi
```

Construire puis lancer le worker depuis la racine du workspace :

```shell
docker build --file apps/worker-fast/Dockerfile \
  --tag transcribe-ai-worker-fast .

docker run --rm --gpus all \
  --network backend-network \
  --env DATABASE_URL=postgresql://postgres:postgres@postgres/postgres \
  --env REDIS_URL=redis://redis:6379/0 \
  --env WORKER_ID=worker-fast-1 \
  --volume transcribe-audio:/data/transcriptions:ro \
  --volume transcribe-models:/models \
  transcribe-ai-worker-fast
```

`backend-network` doit être remplacé par le réseau Docker sur lequel les noms
`postgres` et `redis` sont résolubles. Pour un bind mount, `/models` doit être
inscriptible par l'UID `10001` et les fichiers du volume audio doivent être
lisibles par cet UID. Un volume Docker neuf hérite déjà des permissions définies
dans l'image.

Chaque replica charge sa propre copie du modèle et consomme donc sa propre
mémoire GPU. Le dimensionnement du nombre de replicas doit tenir compte de la
VRAM disponible. Le pilote de l'hôte doit être compatible avec CUDA 12.9 selon
la [matrice NVIDIA](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).

## Healthcheck

L'image déclare un `HEALTHCHECK` Docker qui exécute la commande légère
`worker-healthcheck` toutes les 30 secondes. Elle vérifie PostgreSQL avec
`SELECT 1` et Redis avec `PING`, sans charger Faster-Whisper ni exposer de port
HTTP. Ces deux dépendances sont nécessaires au flow du worker : Redis fournit
les messages et PostgreSQL porte le claim, le lease et la finalisation.

La sonde retourne `0` lorsque les deux services répondent et `1` sinon. Elle
borne ses opérations à cinq secondes, tandis que Docker arrête la commande
après dix secondes et marque le conteneur unhealthy après trois échecs. Le
client Redis et le moteur PostgreSQL créés par chaque sonde sont toujours
refermés. Docker ne redémarre pas automatiquement un conteneur unhealthy : la
politique de redémarrage reste à configurer au niveau du déploiement.

Cette sonde certifie les dépendances, pas la fin du chargement du modèle ni la
progression de la boucle principale. Elle peut donc devenir `healthy` pendant
l'initialisation de Faster-Whisper ; un orchestrateur ne doit pas l'interpréter
seule comme preuve qu'un worker est déjà prêt à consommer.

## Fake de développement

Le fake reste disponible pour les tests et les compositions locales. Son
activation exige les deux variables explicites suivantes :

```shell
WORKER_ID=worker-fast-local \
WORKER_ENVIRONMENT=development \
WORKER_TRANSCRIBER_BACKEND=fake \
uv run --package worker-fast worker-fast
```

Si le fake est demandé en production, le worker échoue clairement sans produire
de faux résultat.

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
uv run pytest -m unit apps/worker-fast/tests
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
```

Le second scénario exerce le runtime commun avec PostgreSQL et Redis réels via
Testcontainers, notamment face à deux messages dupliqués.

Le test d'inférence réellement ML est isolé sous le marker `gpu`. Il télécharge
dans un répertoire temporaire pytest le dialogue français
[A Formal Conversation](https://commons.wikimedia.org/wiki/File:French_Dialogue_-_A_Formal_Conversation.ogg).
La fixture commune aux deux workers vérifie sa taille et son empreinte SHA-256
avant l'inférence. Le test exige une activation explicite, un accès initial à
Hugging Face si le modèle n'est pas en cache et toute la stack GPU décrite
ci-dessus :

```shell
RUN_GPU_TESTS=1 \
uv run --package worker-fast --extra gpu \
  pytest -m gpu apps/worker-fast/tests/gpu
```

Il n'est pas sélectionné par la commande `pytest -m unit` utilisée dans la CI
standard.
