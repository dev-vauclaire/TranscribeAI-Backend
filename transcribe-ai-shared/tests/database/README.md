# Database tests

## Unit tests

These tests do not connect to a database:

```bash
uv run pytest transcribe-ai-shared/tests/database/unit
```

## Integration tests

Integration tests require a dedicated PostgreSQL database. Provide its URL
through `TEST_DATABASE_URL`:

```bash
export TEST_DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/transcribe_ai_test"
uv run pytest transcribe-ai-shared/tests/database/integration
```

The suite creates a unique PostgreSQL schema for each test run and drops it at
the end. Existing schemas and tables are not used.

If `TEST_DATABASE_URL` is absent or targets another database engine, the
integration suite fails explicitly instead of being silently skipped.
