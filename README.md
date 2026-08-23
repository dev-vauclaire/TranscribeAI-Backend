# 📚 Documentation Backend – Transcribe AI

## Sommaire

- [Présentation du backend](#présentation-du-backend)
  - [Objectif du projet](#objectif-du-projet)
  - [Architecture du projet et conventions](#architecture-du-projet-et-conventions)
  - [Stack technique](#stack-technique)
- [Test](#tests)
- [Hooks](#hooks)
- [Conteneurisation](#conteneurisation)

## Présentation du backend

### Objectif du projet

- L'objectif de ce backend est de servir une page web pour
faire de la transcription speech-to-text,
suivant 2 modes : Mono-voice et Multi-voice (diarization).

### Architecture du projet et conventions

Il se compose de six applications et d'un package partagé entre elles.

- [API](apps/api/README.md)
- [Dispatcher](apps/dispatcher/README.md)
- [Maintenance](apps/maintenance/README.md)
- [Migration](apps/migration/README.md)
- [Worker fast](apps/worker-fast/README.md)
- [Worker batch](apps/worker-batch/README.md)
- [Shared](transcribe-ai-shared/README.md)

### Stack technique

- `uv` pour la gestion des dépendances et du workspace
- `Docker` pour la conteneurisation
- `pytest et Testcontainers` pour les tests
- `pre-commit` pour les hooks git

## Tests

```bash
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m system tests/system
```

Les tests d'intégration et système nécessitent Docker. La suite système exige
également `ffprobe` et valide le parcours complet HTTP, PostgreSQL, Redis,
dispatcher, worker et stockage filesystem avec un transcriber déterministe.

## Hooks

## Conteneurisation
