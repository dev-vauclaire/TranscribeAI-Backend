# Tests du package partagé

Les tests unitaires utilisent des doubles et n'accèdent à aucun service ou
fichier réel :

```shell
uv run pytest -m unit transcribe-ai-shared/tests
```

Les tests d'intégration utilisent un dossier temporaire réel, PostgreSQL et
Redis. Les URL doivent pointer vers des instances dédiées aux tests :

```shell
export TEST_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
export TEST_REDIS_URL="redis://localhost:6379/15"
uv run pytest -m integration transcribe-ai-shared/tests
```

La suite PostgreSQL crée puis supprime un schéma unique. La suite Redis crée
puis supprime uniquement des files aux noms uniques.
