# Guide de développement

## Environnement attendu

Le workspace cible Python 3.12 et utilise `uv.lock` comme résolution commune.
Installez :

- `uv` ;
- Docker et Docker Compose ;
- `ffmpeg`, qui fournit aussi `ffprobe` ;
- Git et `pre-commit`.

Docker est nécessaire aux tests Testcontainers. `ffmpeg` encode les fixtures
MP3, OGG et M4A des tests API ; `ffprobe` valide leur contenu et les tests
système.

## Installation sur une nouvelle machine

Depuis la racine du dépôt :

```bash
uv sync --locked --all-packages --all-groups
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

La première commande installe le workspace et les outils de développement,
mais pas les extras ML optionnels des workers. Ne modifiez pas manuellement
`uv.lock`.

Les applications ne chargent pas automatiquement de fichier `.env`. Copiez les
exemples si cela facilite votre shell, puis exportez explicitement les variables
ou utilisez le mécanisme de votre IDE. Ne commitez jamais une copie contenant
un token ou un mot de passe réel.

## Stack Compose locale

Préparez une configuration locale non versionnée, puis démarrez la stack :

```bash
cp .env.compose.example .env
# Remplacer le mot de passe d'exemple dans .env.
docker compose up --build --detach
docker compose ps
```

Compose exécute automatiquement la migration one-shot sur la base vide, puis
démarre l'API et les deux workers avec leur fake. Aucun modèle ML ni token
Hugging Face n'est nécessaire. L'API répond sur le port
`API_PUBLISHED_PORT` et PostgreSQL/Redis ne sont publiés que sur loopback.

Le dispatcher reste une commande ponctuelle :

```bash
docker compose --profile operations run --rm dispatcher
```

La maintenance n'est jamais démarrée comme daemon :

```bash
docker compose --profile maintenance run --rm maintenance
```

Pour tester exactement le manifeste, y compris le build de toutes les images,
la migration d'une base vide et les deux parcours de transcription :

```bash
./scripts/compose-smoke.sh
```

## Parcours local sans moteur ML

Créez d'abord la racine audio :

```bash
mkdir -p tmp/audios_buffers
```

Lancez l'API dans un terminal :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  AUDIO_STORAGE_PATH=tmp/audios_buffers \
  uv run --package api api
```

Lancez le worker voulu avec son fake dans un autre terminal :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  REDIS_URL=redis://localhost:6379/0 \
  AUDIO_STORAGE_PATH=tmp/audios_buffers \
  WORKER_ID=worker-fast-local \
  WORKER_ENVIRONMENT=development \
  WORKER_TRANSCRIBER_BACKEND=fake \
  uv run --package worker-fast worker-fast
```

Pour le profil long-form, remplacez le package, la commande et `WORKER_ID` :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  REDIS_URL=redis://localhost:6379/0 \
  AUDIO_STORAGE_PATH=tmp/audios_buffers \
  WORKER_ID=worker-long-form-local \
  WORKER_ENVIRONMENT=development \
  WORKER_TRANSCRIBER_BACKEND=fake \
  uv run --package worker-long-form-diarization \
  worker-long-form-diarization
```

Créez un job depuis un troisième terminal :

```bash
curl --request POST http://localhost:8000/api/transcriptions \
  --form 'audio_file=@sample.wav' \
  --form 'type=FAST'
```

Le dispatcher est one-shot. Exécutez un cycle après la création :

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres \
  REDIS_URL=redis://localhost:6379/0 \
  uv run --package dispatcher dispatch-transcriptions
```

Consultez ensuite l'URL fournie par l'en-tête `Location`. Pour une boucle de
développement durable, utilisez un scheduler externe ; n'ajoutez pas de boucle
au dispatcher.

## Moteurs ML

Le worker FAST garde le moteur hors de son installation de base afin que le
fake reste léger. Choisissez explicitement l'extra CPU ou GPU :

```bash
uv sync --package worker-fast --extra cpu
uv sync --package worker-fast --extra gpu
```

Le worker long-form garde Torch, WhisperX et Pyannote dans des extras
exclusifs :

```bash
# Choisir exactement une variante
uv sync --package worker-long-form-diarization --extra cpu
uv sync --package worker-long-form-diarization --extra gpu
```

Le backend WhisperX exige un token Hugging Face autorisé à lire
`pyannote/speaker-diarization-community-1`. Le cache de modèles doit viser un
répertoire absolu et inscriptible. Consultez le README du worker avant une
inférence réelle.

Les images Compose réelles utilisent des overrides distincts :

```bash
# CPU (le token est requis pour le worker long-form)
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up --build --detach

# GPU NVIDIA
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build --detach
```

Les deux variantes montent des volumes persistants pour les modèles. La
variante GPU exige en plus le NVIDIA Container Toolkit. Ne placez jamais le
token Hugging Face dans un Dockerfile ou un argument de build.

## Stratégie de tests

### Tests unitaires

Ils n'utilisent ni réseau, ni PostgreSQL, ni Redis, ni modèle réel :

```bash
uv run pytest -m unit
```

Pour une itération rapide, ciblez le composant :

```bash
uv run pytest -m unit apps/api/tests
uv run pytest -m unit apps/dispatcher/tests
uv run pytest -m unit transcribe-ai-shared/tests/worker/unit
```

### Tests d'intégration

Ils utilisent PostgreSQL ou Redis réel via Testcontainers :

```bash
uv run pytest -m integration
```

Ils exigent un daemon Docker accessible. Les tests API nécessitent également
`ffmpeg` et `ffprobe`. Redis n'est pas une dépendance des tests d'intégration de
l'API.

### Tests système

Ils valident les flows FAST et LONG_FORM_DIARIZATION avec les vrais composants
d'infrastructure et un `FakeTranscriber` déterministe :

```bash
uv run pytest -m system tests/system
```

### Tests GPU

Ils sont exclus de la CI standard et nécessitent une activation explicite :

```bash
RUN_GPU_TESTS=1 \
uv run --package worker-fast --extra gpu \
  pytest -m gpu apps/worker-fast/tests/gpu
```

```bash
RUN_GPU_TESTS=1 \
WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN=hf_xxx \
uv run --package worker-long-form-diarization --extra gpu \
  pytest -m gpu apps/worker-long-form-diarization/tests/gpu
```

Ces tests téléchargent un petit audio public vérifié. Le worker long-form
télécharge ses modèles dans un dossier pytest temporaire. Un accès réseau, un
runner NVIDIA correctement configuré et suffisamment d'espace disque sont
nécessaires.

## Qualité locale

Avant une demande de validation :

```bash
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
git diff --check
```

Le hook pre-push exécute aussi Semgrep. La CI actuelle vérifie le lint et les
tests `unit`. Les suites d'intégration, système et GPU restent à exécuter dans
un environnement adapté tant qu'aucun job CI dédié ne les couvre.

## Ajouter une modification

### Nouvelle variable d'environnement

1. l'ajouter à la classe `BaseSettings` propriétaire ;
2. la valider au démarrage ;
3. compléter le `.env.example` de l'application ;
4. compléter la table du README de cette application ;
5. tester valeur valide, défaut et valeur refusée.

### Changement PostgreSQL

1. modifier le modèle SQLAlchemy ;
2. créer une révision Alembic explicite ;
3. ajouter les tests upgrade et downgrade depuis la révision précédente ;
4. adapter repositories, services et tests d'intégration ;
5. vérifier les index associés aux requêtes de travail.

### Changement de flow

1. identifier le composant propriétaire ;
2. préserver les frontières transactionnelles de
   [workflows.md](workflows.md) ;
3. ajouter un test unitaire d'orchestration ;
4. ajouter un test d'intégration pour la concurrence ou la durabilité ;
5. mettre à jour le README du composant et la documentation transverse.

## Conventional Commits

Utilisez le scope qui possède réellement le changement, par exemple :

```text
feat(api): ...
feat(dispatcher): ...
feat(worker): ...
feat(common): ...
test(system): ...
docs: ...
```

Ne mélangez pas dans un même commit un refactoring sans rapport et une
fonctionnalité. Ne créez ou ne poussez un commit qu'après validation explicite.
