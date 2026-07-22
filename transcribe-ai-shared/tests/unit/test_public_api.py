import pytest

from transcribe_ai_shared import (
    AudioManager,
    DatabaseConfig,
    Job,
    JobRepository,
    JobStatus,
    JobType,
    RedisQueueService,
    SessionFactory,
    check_postgres_connection,
    create_db_engine,
    create_session_factory,
    transaction,
)


pytestmark = pytest.mark.unit


def test_supported_public_imports_are_available():
    assert AudioManager is not None
    assert DatabaseConfig is not None
    assert Job is not None
    assert JobRepository is not None
    assert JobStatus is not None
    assert JobType is not None
    assert RedisQueueService is not None
    assert SessionFactory is not None
    assert check_postgres_connection is not None
    assert create_db_engine is not None
    assert create_session_factory is not None
    assert transaction is not None
