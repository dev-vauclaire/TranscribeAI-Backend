# Database tests

## Unit tests

Lancer les tests unitaires :

```bash
uv run pytest transcribe-ai-shared/tests/database/unit
```

## Integration tests

Les tests d'intégration nécessitent un moteur Docker accessible. Testcontainers
démarre et arrête automatiquement une instance PostgreSQL 16 dédiée.

Lancer les tests d'intégration :

```bash
uv run pytest transcribe-ai-shared/tests/database/integration
```

> Ces commandes sont à lancer à la racine du projet
