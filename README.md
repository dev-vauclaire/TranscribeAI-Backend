# Transcribe AI Backend

Backend asynchrone de transcription audio en français. Il propose deux profils :

- `FAST`, optimisé pour les fichiers courts et la latence ;
- `LONG_FORM_DIARIZATION`, destiné aux fichiers longs avec identification des
  locuteurs.

PostgreSQL porte l'état métier durable. Redis Streams transporte les demandes
vers les workers. Les fichiers audio sont actuellement conservés sur un volume
partagé derrière une abstraction remplaçable par un stockage objet.

## Commencer ici

Pour découvrir le projet ou reprendre le travail dans une nouvelle session :

1. lire [AGENTS.md](AGENTS.md) pour les règles de contribution ;
2. lire la [vue d'architecture](docs/architecture.md) ;
3. lire les [workflows et invariants](docs/workflows.md) ;
4. consulter les [contrats de transcription](docs/transcription-contracts.md)
   pour une modification des workers ou de l'API ;
5. ouvrir ensuite le README du composant concerné ;
6. inspecter `git status`, `git diff` et les derniers commits avant toute
   modification.

Le [guide de développement](docs/development.md) décrit l'installation, les
commandes et les tests. Le [guide d'exploitation](docs/operations.md) explique
les processus, probes, volumes et logs.

## Composants

- [API](apps/api/README.md) : upload, validation, création et consultation des
  jobs. Elle dépend de PostgreSQL et du stockage, jamais de Redis.
- [Dispatcher](apps/dispatcher/README.md) : cycle one-shot de recovery,
  réconciliation puis publication Redis.
- [Worker FAST](apps/worker-fast/README.md) : Faster-Whisper,
  `large-v3-turbo`, langue française et VAD.
- [Worker long-form diarization](apps/worker-long-form-diarization/README.md) :
  WhisperX, alignement et Pyannote.
- [Maintenance](apps/maintenance/README.md) : nettoyage conservateur du volume
  audio sous forme de tâche one-shot.
- [Migration](apps/migration/README.md) : application Alembic one-shot.
- [Package partagé](transcribe-ai-shared/README.md) : modèles, repositories,
  stockage, Redis Streams et runtime commun des workers.

Les applications dépendent du package partagé. Celui-ci ne doit pas dépendre
d'une application.

## Prérequis

- Python 3.12 ;
- [uv](https://docs.astral.sh/uv/) ;
- Docker avec le plugin Compose ;
- `ffmpeg`, qui fournit `ffprobe`, pour l'API et les tests média ;
- un GPU NVIDIA et le NVIDIA Container Toolkit uniquement pour les tests ou
  workers GPU.

Depuis la racine du dépôt :

```bash
uv sync --locked --all-packages --all-groups
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
cp .env.compose.example .env
# Remplacer le mot de passe d'exemple, puis :
docker compose up --build --detach
```

Le Compose par défaut démarre PostgreSQL, Redis, la migration, l'API et les
deux workers avec `FakeTranscriber`. Il valide donc le backend complet sans
télécharger les modèles ni exiger de GPU. La migration doit terminer avec
succès avant le démarrage des applications.

Le dispatcher reste intentionnellement one-shot : après la création de jobs,
un scheduler externe ou cette commande déclenche un cycle :

```bash
docker compose --profile operations run --rm dispatcher
```

La maintenance suit la même convention, sous un profil séparé :

```bash
docker compose --profile maintenance run --rm maintenance
```

Les variantes ML réelles sont explicites : `docker-compose.cpu.yml` utilise
les cibles CPU, tandis que `docker-compose.gpu.yml` réserve les GPU NVIDIA et
monte les caches modèles persistants. Le worker long-form exige alors un token
Hugging Face fourni hors du dépôt.

## Commandes principales

```bash
# API
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  AUDIO_STORAGE_PATH=tmp/audios_buffers \
  uv run --package api api

# Un cycle de dispatch
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  REDIS_URL=redis://localhost:6379/0 \
  uv run --package dispatcher dispatch-transcriptions

# Nettoyage one-shot
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  AUDIO_STORAGE_PATH=tmp/audios_buffers \
  uv run --package maintenance cleanup-storage
```

Les workers réels et leurs variantes de développement sont documentés dans
leurs README respectifs.

## Tests et qualité

```bash
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m system tests/system
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
./scripts/compose-smoke.sh
```

Les tests d'intégration et système nécessitent Docker. La suite système exige
également `ffprobe` et valide le parcours HTTP, PostgreSQL, Redis, dispatcher,
worker et stockage avec un transcriber déterministe.

Les tests ML réels utilisent le marker `gpu` et restent exclus de la CI
standard. Leurs commandes et prérequis sont documentés dans les README des
workers. Le smoke Compose construit toutes les images de service, migre une
base vide, attend la readiness puis vérifie les flows FAST et
LONG_FORM_DIARIZATION avec les transcripteurs factices.

## Démarrer un nouveau prompt

Un prompt de reprise peut rester court, car le contexte stable vit dans le
dépôt :

```text
Lis AGENTS.md, docs/architecture.md, docs/workflows.md et le README du
composant concerné. Inspecte ensuite l'état Git et le code autour du diff.

Mode demandé : revue uniquement | proposition | implémentation.
Objectif : ...
Contraintes ou décisions déjà prises : ...
Tests exécutés : ...
Commit prévu : ...
```

Ne placez pas de secret, token, URL contenant un mot de passe ou transcription
réelle dans ce prompt ou dans la documentation versionnée.
