# Consignes locales — Worker FAST

Lire [README.md](README.md), les
[workflows](../../docs/workflows.md) et les
[contrats](../../docs/transcription-contracts.md).

## Frontières

- Cette application choisit `JobType.FAST` et compose Faster-Whisper.
- Le runtime Redis/PostgreSQL reste dans `transcribe-ai-shared.worker`.
- L'adaptateur ML connaît seulement `AudioStorage` et `TranscriptionOutput`.
- Ne pas ajouter HTTP, heartbeat, ACK ou retry dans l'adaptateur.

## Invariants

- Langue fixée à `fr` et `vad_filter=true`.
- Modèle par défaut `large-v3-turbo`.
- Déporter l'appel et l'itération lazy du moteur hors de l'event loop.
- Garder le flux audio ouvert pendant la consommation des segments.
- Le fake est autorisé uniquement avec l'environnement `development`.
- Aucun téléchargement de modèle dans les tests unitaires.
- Les tests ML réels portent `pytest.mark.gpu` et exigent un opt-in.

## Validation

```bash
uv run pytest -m unit apps/worker-fast/tests
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
```

Toute modification du runtime partagé doit aussi être validée avec le worker
long-form.
