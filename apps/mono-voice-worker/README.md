# Mono voice worker

Le worker est lancé depuis la racine du workspace avec :

```shell
uv run --package mono-voice-worker mono-voice-worker
```

Les variables disponibles sont documentées dans `.env.example`.

Le démarrage échoue si PostgreSQL, Redis ou Whisper est indisponible. Le
redémarrage du processus est pris en charge par l'orchestrateur.

## Tests

Les tests unitaires n'utilisent ni PostgreSQL, ni Redis, ni appel HTTP réel :

```shell
uv run pytest -m unit apps/mono-voice-worker/tests
```

Le test d'intégration complet du worker avec ses trois services externes est
prévu en P1.
