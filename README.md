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

Il se compose de 3 applications et d'un package partagé entre elles.

- [API](apps/transcribe-ai-api/README.md)
- [Worker mono voice](apps/transcribe-ai-worker/README.md)
- [Worker multi voice](apps/multi-voice-worker/README.md)
- [Shared](apps/transcribe-ai-shared/README.md)

### Stack technique

- `uv` pour la gestion des dépendances et du workspace
- `Docker` pour la conteneurisation
- `pytest et Testcontainers` pour les tests
- `pre-commit` pour les hooks git

## Tests

```bash
uv run pytest
```

## Hooks

## Conteneurisation
