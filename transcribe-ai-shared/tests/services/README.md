# Service tests

## Unit tests

Les tests unitaires de `RedisQueueService` utilisent un client simulé :

```bash
uv run pytest transcribe-ai-shared/tests/services/unit
```

## Integration tests

The `AudioManager` tests use a real temporary directory managed by pytest.

The `RedisQueueService` tests require a real Redis instance configured through
`TEST_REDIS_URL`:

```bash
export TEST_REDIS_URL="redis://localhost:6379/15"
uv run pytest transcribe-ai-shared/tests/services/integration
```

Each Redis test uses a randomly named queue and deletes only that queue during
cleanup. Existing Redis keys are not flushed or modified.

If `TEST_REDIS_URL` is absent or unreachable, Redis integration tests fail
explicitly instead of being silently skipped.
