# Tests d'intégration des workers

La mécanique Redis Streams et le claim PostgreSQL sont communs aux profils
FAST et LONG_FORM_DIARIZATION.
Leurs tests d'intégration vivent donc dans :

```text
transcribe-ai-shared/tests/worker/integration
```

L'adaptateur Faster-Whisper est maintenant couvert par des tests unitaires avec
un moteur injecté et par un smoke GPU opt-in. Aucun test d'intégration propre à
l'application n'est nécessaire tant que sa composition reste limitée au choix
du type et de l'adaptateur.

```bash
uv run pytest -m integration transcribe-ai-shared/tests/worker/integration
uv run pytest -m unit apps/worker-fast/tests
```

Si une future fonctionnalité FAST ajoute une frontière réelle qui n'existe pas
dans le runtime partagé, ses tests d'intégration devront vivre dans ce dossier.
