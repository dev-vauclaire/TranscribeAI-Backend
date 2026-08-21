import pytest

from transcribe_ai_shared import (
    AudioAlreadyExistsError,
    AudioDirectoryChangedError,
    AudioLocation,
    AudioNotFoundError,
    AudioStorage,
    AudioStorageMaintenance,
    AutoClaimResult,
    AsyncSessionFactory,
    ClaimedJob,
    DatabaseSettings,
    FileSystemAudioStorage,
    InvalidAudioLocationError,
    InvalidJobStreamMessageError,
    JobRepository,
    JobStreamMessage,
    JobStatus,
    JobType,
    PostgresWorkerJobStore,
    RedisConnectionError,
    RedisOperationError,
    RedisSettings,
    RedisTranscriptionStreams,
    ReceivedJobStreamMessage,
    ResultRepository,
    SessionFactory,
    StorageScanResult,
    StorageSettings,
    TranscriptionDirectory,
    Transcriber,
    TranscriptionExecutionError,
    TranscriptionOutput,
    TranscriptionJob,
    TranscriptionJobSchema,
    TranscriptionResult,
    TranscriptionResultSchema,
    TranscriptionStreamName,
    TranscriptionStreams,
    WorkerClaimRejected,
    WorkerIdle,
    WorkerJobStore,
    WorkerJobTypeMismatchError,
    WorkerProcessResult,
    WorkerRuntime,
    WorkerSettings,
    WorkerTranscribed,
    async_transaction,
    check_postgres_connection,
    create_async_db_engine,
    create_async_session_factory,
    create_db_engine,
    create_session_factory,
    run_worker,
    transaction,
    stream_name_for_job_type,
)


pytestmark = pytest.mark.unit


def test_supported_public_imports_are_available():
    assert AudioAlreadyExistsError is not None
    assert AudioDirectoryChangedError is not None
    assert AudioLocation is not None
    assert AudioNotFoundError is not None
    assert AudioStorage is not None
    assert AudioStorageMaintenance is not None
    assert AutoClaimResult is not None
    assert AsyncSessionFactory is not None
    assert ClaimedJob is not None
    assert DatabaseSettings is not None
    assert FileSystemAudioStorage is not None
    assert InvalidAudioLocationError is not None
    assert InvalidJobStreamMessageError is not None
    assert JobStatus is not None
    assert JobType is not None
    assert JobRepository is not None
    assert JobStreamMessage is not None
    assert PostgresWorkerJobStore is not None
    assert ReceivedJobStreamMessage is not None
    assert RedisConnectionError is not None
    assert RedisOperationError is not None
    assert RedisSettings is not None
    assert RedisTranscriptionStreams is not None
    assert ResultRepository is not None
    assert SessionFactory is not None
    assert StorageScanResult is not None
    assert StorageSettings is not None
    assert TranscriptionDirectory is not None
    assert Transcriber is not None
    assert TranscriptionExecutionError is not None
    assert TranscriptionOutput is not None
    assert TranscriptionJob is not None
    assert TranscriptionJobSchema is not None
    assert TranscriptionResult is not None
    assert TranscriptionResultSchema is not None
    assert TranscriptionStreamName is not None
    assert TranscriptionStreams is not None
    assert WorkerClaimRejected is not None
    assert WorkerIdle is not None
    assert WorkerJobStore is not None
    assert WorkerJobTypeMismatchError is not None
    assert WorkerProcessResult is not None
    assert WorkerRuntime is not None
    assert WorkerSettings is not None
    assert WorkerTranscribed is not None
    assert async_transaction is not None
    assert check_postgres_connection is not None
    assert create_async_db_engine is not None
    assert create_async_session_factory is not None
    assert create_db_engine is not None
    assert create_session_factory is not None
    assert run_worker is not None
    assert transaction is not None
    assert stream_name_for_job_type is not None
