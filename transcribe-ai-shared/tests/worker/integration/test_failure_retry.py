from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dispatcher.postgresql import PostgresDispatchJobStore
from dispatcher.service import DispatcherService
from transcribe_ai_shared import (
    JobRepository,
    JobStatus,
    JobStreamMessage,
    JobType,
    PermanentTranscriptionError,
    PostgresWorkerJobStore,
    ReceivedJobStreamMessage,
    RetryableTranscriptionError,
    TranscriptionCompletionService,
    TranscriptionFailureService,
    TranscriptionJob,
    TranscriptionStreams,
    WorkerClaimRejected,
    WorkerFailed,
    WorkerRetryScheduled,
    WorkerRuntime,
    async_transaction,
)
from transcribe_ai_shared.worker.testing import FakeTranscriber

from .conftest import WorkerRedisContext


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAST_STREAM = "transcription:fast"
GROUP_NAME = "transcription-workers"
WORKER_ID = "worker-fast-1"
RECOVERY_WORKER_ID = "worker-fast-recovery-1"
JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
NOW = datetime(2099, 1, 1, 12, tzinfo=UTC)
DISPATCHED_AT = NOW + timedelta(minutes=10)
LEASE_DURATION = timedelta(minutes=5)
MAX_ATTEMPTS = 3


class CommitObservingStreams:
    """Vérifie depuis une autre session que PostgreSQL est commité avant l'ACK."""

    def __init__(
        self,
        delegate: TranscriptionStreams,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        expected_status: JobStatus,
        expected_attempt_count: int,
        expected_error_code: str,
    ) -> None:
        self._delegate = delegate
        self._session_factory = session_factory
        self._expected_status = expected_status
        self._expected_attempt_count = expected_attempt_count
        self._expected_error_code = expected_error_code
        self.ack_observed_committed_transition = False

    async def ensure_consumer_group(
        self,
        job_type: JobType,
        group_name: str,
    ) -> None:
        await self._delegate.ensure_consumer_group(job_type, group_name)

    async def consume(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        block_milliseconds: int | None = 5_000,
    ) -> ReceivedJobStreamMessage | None:
        return await self._delegate.consume(
            job_type,
            group_name,
            consumer_name,
            block_milliseconds=block_milliseconds,
        )

    async def ack_and_delete(
        self,
        group_name: str,
        message: ReceivedJobStreamMessage,
    ) -> bool:
        async with self._session_factory() as session:
            job = await JobRepository(session).get_by_uuid(message.job_uuid)

        assert job is not None
        assert job.status is self._expected_status
        assert job.attempt_count == self._expected_attempt_count
        assert job.dispatch_required is (self._expected_status is JobStatus.QUEUED)
        assert job.lease_owner is None
        assert job.lease_expires_at is None
        assert job.last_error == self._expected_error_code
        self.ack_observed_committed_transition = True
        return await self._delegate.ack_and_delete(group_name, message)


class SimulatedWorkerCrash(RuntimeError):
    """Interrompt le worker après le commit PostgreSQL, avant l'ACK Redis."""


class CrashBeforeAckStreams:
    def __init__(self, delegate: TranscriptionStreams) -> None:
        self._delegate = delegate

    async def ensure_consumer_group(
        self,
        job_type: JobType,
        group_name: str,
    ) -> None:
        await self._delegate.ensure_consumer_group(job_type, group_name)

    async def consume(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        block_milliseconds: int | None = 5_000,
    ) -> ReceivedJobStreamMessage | None:
        return await self._delegate.consume(
            job_type,
            group_name,
            consumer_name,
            block_milliseconds=block_milliseconds,
        )

    async def ack_and_delete(
        self,
        group_name: str,
        message: ReceivedJobStreamMessage,
    ) -> bool:
        del group_name, message
        raise SimulatedWorkerCrash("worker stopped before Redis ACK")


def make_job(*, attempt_count: int) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=JOB_UUID,
        status=JobStatus.QUEUED,
        job_type=JobType.FAST,
        audio_uri=f"{JOB_UUID}/input.wav",
        dispatch_required=False,
        attempt_count=attempt_count,
    )


async def persist_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    attempt_count: int,
) -> None:
    async with async_transaction(session_factory) as session:
        await JobRepository(session).add(make_job(attempt_count=attempt_count))


async def load_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> TranscriptionJob:
    async with session_factory() as session:
        job = await JobRepository(session).get_by_uuid(JOB_UUID)
        assert job is not None
        return job


def make_runtime(
    *,
    streams: TranscriptionStreams,
    session_factory: async_sessionmaker[AsyncSession],
    transcriber: FakeTranscriber,
    max_attempts: int = MAX_ATTEMPTS,
    worker_id: str = WORKER_ID,
) -> WorkerRuntime:
    return WorkerRuntime(
        streams=streams,
        job_store=PostgresWorkerJobStore(
            session_factory,
            expected_job_type=JobType.FAST,
        ),
        transcriber=transcriber,
        completer=TranscriptionCompletionService(session_factory),
        failure_handler=TranscriptionFailureService(
            session_factory,
            max_attempts=max_attempts,
        ),
        job_type=JobType.FAST,
        group_name=GROUP_NAME,
        worker_id=worker_id,
        lease_duration=LEASE_DURATION,
        clock=lambda: NOW,
    )


async def publish_attempt(
    worker_redis: WorkerRedisContext,
    *,
    attempt_count: int,
) -> str:
    return await worker_redis.streams.publish(
        JobStreamMessage(
            job_uuid=JOB_UUID,
            job_type=JobType.FAST,
            attempt_count=attempt_count,
        )
    )


async def assert_old_message_removed(worker_redis: WorkerRedisContext) -> None:
    pending = await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME)
    assert pending["pending"] == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []


async def test_retryable_failure_commits_requeue_then_dispatches_a_new_attempt(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    error_code = "MODEL_TEMPORARILY_UNAVAILABLE"
    await persist_job(async_session_factory, attempt_count=0)
    observed_streams = CommitObservingStreams(
        worker_redis.streams,
        async_session_factory,
        expected_status=JobStatus.QUEUED,
        expected_attempt_count=1,
        expected_error_code=error_code,
    )
    transcriber = FakeTranscriber(
        error=RetryableTranscriptionError(error_code),
    )
    runtime = make_runtime(
        streams=observed_streams,
        session_factory=async_session_factory,
        transcriber=transcriber,
    )
    await runtime.initialize()
    old_message_id = await publish_attempt(worker_redis, attempt_count=0)

    result = await runtime.process_next(block_milliseconds=100)

    assert isinstance(result, WorkerRetryScheduled)
    assert result.message.redis_message_id == old_message_id
    assert result.job.attempt_count == 0
    assert result.next_attempt_count == 1
    assert result.failure.error_code == error_code
    assert result.removed_from_stream is True
    assert observed_streams.ack_observed_committed_transition is True
    assert transcriber.calls == (result.job.audio_location,)

    requeued_job = await load_job(async_session_factory)
    assert requeued_job.status is JobStatus.QUEUED
    assert requeued_job.attempt_count == 1
    assert requeued_job.dispatch_required is True
    assert requeued_job.lease_owner is None
    assert requeued_job.lease_expires_at is None
    assert requeued_job.last_error == error_code
    assert requeued_job.completed_at is None
    await assert_old_message_removed(worker_redis)

    dispatch_result = await DispatcherService(
        job_store=PostgresDispatchJobStore(async_session_factory),
        streams=worker_redis.streams,
        clock=lambda: DISPATCHED_AT,
    ).dispatch_batch(10)

    assert dispatch_result.selected_count == 1
    assert dispatch_result.published_count == 1
    assert dispatch_result.confirmed_count == 1
    entries = await worker_redis.client.xrange(FAST_STREAM)
    assert len(entries) == 1
    new_message_id, payload = entries[0]
    assert new_message_id != old_message_id
    assert payload == {
        "job_uuid": str(JOB_UUID),
        "attempt_count": "1",
    }
    dispatched_job = await load_job(async_session_factory)
    assert dispatched_job.status is JobStatus.QUEUED
    assert dispatched_job.attempt_count == 1
    assert dispatched_job.dispatch_required is False
    assert dispatched_job.last_dispatched_at == DISPATCHED_AT


async def test_requeued_attempt_is_not_inferred_again_when_ack_crashes(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    await persist_job(async_session_factory, attempt_count=0)
    transcriber = FakeTranscriber(
        error=RetryableTranscriptionError("MODEL_TEMPORARILY_UNAVAILABLE"),
    )
    first_runtime = make_runtime(
        streams=CrashBeforeAckStreams(worker_redis.streams),
        session_factory=async_session_factory,
        transcriber=transcriber,
    )
    await first_runtime.initialize()
    old_message_id = await publish_attempt(worker_redis, attempt_count=0)

    with pytest.raises(SimulatedWorkerCrash, match="before Redis ACK"):
        await first_runtime.process_next(block_milliseconds=100)

    committed_job = await load_job(async_session_factory)
    assert committed_job.status is JobStatus.QUEUED
    assert committed_job.attempt_count == 1
    assert committed_job.dispatch_required is True
    pending_before_recovery = await worker_redis.client.xpending(
        FAST_STREAM,
        GROUP_NAME,
    )
    assert pending_before_recovery["pending"] == 1
    assert pending_before_recovery["min"] == old_message_id

    # Le seuil nul simule ici l'expiration Redis sans ralentir le test. En
    # production, le récupérateur devra utiliser un délai d'inactivité réel.
    auto_claimed = await worker_redis.streams.autoclaim(
        JobType.FAST,
        GROUP_NAME,
        RECOVERY_WORKER_ID,
        min_idle_milliseconds=0,
    )
    assert len(auto_claimed.messages) == 1
    recovered_message = auto_claimed.messages[0]
    assert recovered_message.redis_message_id == old_message_id

    recovery_runtime = make_runtime(
        streams=worker_redis.streams,
        session_factory=async_session_factory,
        transcriber=transcriber,
        worker_id=RECOVERY_WORKER_ID,
    )
    recovered = await recovery_runtime.process_message(recovered_message)

    assert isinstance(recovered, WorkerClaimRejected)
    assert recovered.removed_from_stream is True
    assert len(transcriber.calls) == 1
    await assert_old_message_removed(worker_redis)


async def test_permanent_failure_is_committed_then_acknowledged_without_redispatch(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    error_code = "UNSUPPORTED_AUDIO"
    await persist_job(async_session_factory, attempt_count=0)
    observed_streams = CommitObservingStreams(
        worker_redis.streams,
        async_session_factory,
        expected_status=JobStatus.FAILED,
        expected_attempt_count=0,
        expected_error_code=error_code,
    )
    runtime = make_runtime(
        streams=observed_streams,
        session_factory=async_session_factory,
        transcriber=FakeTranscriber(
            error=PermanentTranscriptionError(error_code),
        ),
    )
    await runtime.initialize()
    await publish_attempt(worker_redis, attempt_count=0)

    result = await runtime.process_next(block_milliseconds=100)

    assert isinstance(result, WorkerFailed)
    assert result.failure.error_code == error_code
    assert result.removed_from_stream is True
    assert observed_streams.ack_observed_committed_transition is True
    failed_job = await load_job(async_session_factory)
    assert failed_job.status is JobStatus.FAILED
    assert failed_job.attempt_count == 0
    assert failed_job.dispatch_required is False
    assert failed_job.lease_owner is None
    assert failed_job.lease_expires_at is None
    assert failed_job.last_error == error_code
    assert failed_job.completed_at is not None
    await assert_old_message_removed(worker_redis)

    dispatch_result = await DispatcherService(
        job_store=PostgresDispatchJobStore(async_session_factory),
        streams=worker_redis.streams,
        clock=lambda: DISPATCHED_AT,
    ).dispatch_batch(10)

    assert dispatch_result.selected_count == 0
    assert dispatch_result.published_count == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []


async def test_last_retryable_attempt_becomes_failed_without_redispatch(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    error_code = "MODEL_TEMPORARILY_UNAVAILABLE"
    last_attempt_count = MAX_ATTEMPTS - 1
    await persist_job(
        async_session_factory,
        attempt_count=last_attempt_count,
    )
    observed_streams = CommitObservingStreams(
        worker_redis.streams,
        async_session_factory,
        expected_status=JobStatus.FAILED,
        expected_attempt_count=last_attempt_count,
        expected_error_code=error_code,
    )
    runtime = make_runtime(
        streams=observed_streams,
        session_factory=async_session_factory,
        transcriber=FakeTranscriber(
            error=RetryableTranscriptionError(error_code),
        ),
    )
    await runtime.initialize()
    await publish_attempt(
        worker_redis,
        attempt_count=last_attempt_count,
    )

    result = await runtime.process_next(block_milliseconds=100)

    assert isinstance(result, WorkerFailed)
    assert result.job.attempt_count == last_attempt_count
    assert result.failure.error_code == error_code
    assert result.removed_from_stream is True
    assert observed_streams.ack_observed_committed_transition is True
    failed_job = await load_job(async_session_factory)
    assert failed_job.status is JobStatus.FAILED
    assert failed_job.attempt_count == last_attempt_count
    assert failed_job.dispatch_required is False
    assert failed_job.last_error == error_code
    await assert_old_message_removed(worker_redis)

    dispatch_result = await DispatcherService(
        job_store=PostgresDispatchJobStore(async_session_factory),
        streams=worker_redis.streams,
        clock=lambda: DISPATCHED_AT,
    ).dispatch_batch(10)

    assert dispatch_result.selected_count == 0
    assert dispatch_result.published_count == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []
