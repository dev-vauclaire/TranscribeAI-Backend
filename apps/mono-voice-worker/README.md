# Mono voice worker

Le worker est lancé depuis la racine du workspace avec :

```shell
uv run --package mono-voice-worker mono-voice-worker
```

Les variables disponibles sont documentées dans `.env.example`.

Le démarrage échoue si PostgreSQL, Redis ou Whisper est indisponible. Le
redémarrage du processus est pris en charge par l'orchestrateur.
