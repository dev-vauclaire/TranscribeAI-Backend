import pytest

from transcribe_ai_shared import (
    AudioAlreadyExistsError,
    AudioDirectoryChangedError,
    AudioLocation,
    AudioNotFoundError,
    AudioStorage,
    AudioStorageMaintenance,
    AsyncSessionFactory,
    DatabaseSettings,
    FileSystemAudioStorage,
    InvalidAudioLocationError,
    JobRepository,
    JobStatus,
    JobType,
    RedisConnectionError,
    RedisQueueService,
    RedisSettings,
    ResultRepository,
    SessionFactory,
    StorageScanResult,
    StorageSettings,
    TranscriptionDirectory,
    TranscriptionJob,
    TranscriptionJobSchema,
    TranscriptionResult,
    TranscriptionResultSchema,
    WorkerSettings,
    async_transaction,
    check_postgres_connection,
    create_async_db_engine,
    create_async_session_factory,
    create_db_engine,
    create_session_factory,
    transaction,
)


pytestmark = pytest.mark.unit


def test_supported_public_imports_are_available():
    assert AudioAlreadyExistsError is not None
    assert AudioDirectoryChangedError is not None
    assert AudioLocation is not None
    assert AudioNotFoundError is not None
    assert AudioStorage is not None
    assert AudioStorageMaintenance is not None
    assert AsyncSessionFactory is not None
    assert DatabaseSettings is not None
    assert FileSystemAudioStorage is not None
    assert InvalidAudioLocationError is not None
    assert JobStatus is not None
    assert JobType is not None
    assert JobRepository is not None
    assert RedisConnectionError is not None
    assert RedisQueueService is not None
    assert RedisSettings is not None
    assert ResultRepository is not None
    assert SessionFactory is not None
    assert StorageScanResult is not None
    assert StorageSettings is not None
    assert TranscriptionDirectory is not None
    assert TranscriptionJob is not None
    assert TranscriptionJobSchema is not None
    assert TranscriptionResult is not None
    assert TranscriptionResultSchema is not None
    assert WorkerSettings is not None
    assert async_transaction is not None
    assert check_postgres_connection is not None
    assert create_async_db_engine is not None
    assert create_async_session_factory is not None
    assert create_db_engine is not None
    assert create_session_factory is not None
    assert transaction is not None
