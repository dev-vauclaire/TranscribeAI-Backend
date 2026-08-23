# Consignes locales — Worker long-form diarization

Lire [README.md](README.md), les
[workflows](../../docs/workflows.md) et les
[contrats](../../docs/transcription-contracts.md).

## Frontières

- Cette application choisit `JobType.LONG_FORM_DIARIZATION` et compose
  WhisperX/Pyannote.
- Le runtime Redis/PostgreSQL reste dans `transcribe-ai-shared.worker`.
- Les imports ML restent différés pour que le package de base et les tests
  unitaires fonctionnent sans extra CPU ou GPU.
- L'adaptateur ne connaît ni Redis, ni PostgreSQL, ni heartbeat, ni HTTP.

## Invariants

- Langue fixée à `fr`.
- Modèles ASR autorisés : `large-v3-turbo` et `large-v3`.
- Diarisation fixée à `pyannote/speaker-diarization-community-1`.
- Déporter le pipeline synchrone complet hors de l'event loop.
- Nettoyer le fichier audio temporaire quelle que soit l'issue.
- Fusionner les segments adjacents du même speaker connu.
- Un speaker absent constitue une frontière de fusion.
- Le fake reste réservé au développement.
- Aucun modèle ou token réel dans les tests unitaires.

## Validation

```bash
uv run pytest -m unit apps/worker-long-form-diarization/tests
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
```

Les extras `cpu` et `gpu` sont mutuellement exclusifs. Les tests GPU utilisent
un cache de modèles temporaire et nécessitent un token Hugging Face.
