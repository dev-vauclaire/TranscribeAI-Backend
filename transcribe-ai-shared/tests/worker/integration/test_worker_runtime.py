import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from transcribe_ai_shared import (
    AudioLocation,
    ClaimedJob,
    JobRepository,
    JobStatus,
    JobStreamMessage,
    JobType,
    PostgresWorkerJobStore,
    ResultRepository,
    TranscriptionCompletionService,
    TranscriptionFailureService,
    TranscriptionJob,
    TranscriptionOutput,
    TranscriptionResult,
    TranscriptionStreams,
    WorkerClaimDeferred,
    WorkerClaimRejected,
    WorkerCompleted,
    WorkerIdle,
    WorkerJobStore,
    WorkerJobTypeMismatchError,
    WorkerRuntime,
    async_transaction,
)
from transcribe_ai_shared.worker.testing import FakeTranscriber

from .conftest import WorkerRedisContext


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAST_STREAM = "transcription:fast"
BATCH_STREAM = "transcription:batch"
GROUP_NAME = "transcription-workers"
FIRST_WORKER_ID = "worker-fast-1"
SECOND_WORKER_ID = "worker-fast-2"
JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
NOW = datetime(2099, 1, 1, 12, tzinfo=UTC)
CLOCK_STEP = timedelta(seconds=1)
LEASE_DURATION = timedelta(minutes=5)


class AdvancingClock:
    """Fournit des échéances croissantes sans dépendre de l'horloge du test."""

    def __init__(self) -> None:
        self._current = NOW

    def __call__(self) -> datetime:
        current = self._current
        self._current += CLOCK_STEP
        return current


def make_job(*, job_type: JobType = JobType.FAST) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=JOB_UUID,
        status=JobStatus.QUEUED,
        job_type=job_type,
        audio_uri=f"{JOB_UUID}/input.wav",
        dispatch_required=False,
        attempt_count=0,
    )


async def persist_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_type: JobType = JobType.FAST,
) -> None:
    async with async_transaction(session_factory) as session:
        await JobRepository(session).add(make_job(job_type=job_type))


async def load_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> TranscriptionJob:
    async with session_factory() as session:
        job = await JobRepository(session).get_by_uuid(JOB_UUID)
        assert job is not None
        return job


async def load_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> TranscriptionResult | None:
    async with session_factory() as session:
        return await ResultRepository(session).get_by_job_uuid(JOB_UUID)


def make_runtime(
    *,
    streams: TranscriptionStreams,
    job_store: WorkerJobStore,
    transcriber: FakeTranscriber,
    worker_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    job_type: JobType = JobType.FAST,
) -> WorkerRuntime:
    return WorkerRuntime(
        streams=streams,
        job_store=job_store,
        transcriber=transcriber,
        completer=TranscriptionCompletionService(session_factory),
        failure_handler=TranscriptionFailureService(
            session_factory,
            max_attempts=3,
        ),
        job_type=job_type,
        group_name=GROUP_NAME,
        worker_id=worker_id,
        lease_duration=LEASE_DURATION,
        clock=AdvancingClock(),
    )


class SynchronizedClaimStore:
    """Force deux consumers à atteindre le vrai claim avant son exécution."""

    def __init__(self, delegate: WorkerJobStore) -> None:
        self._delegate = delegate
        self._lock = asyncio.Lock()
        self._both_consumers_ready = asyncio.Event()
        self.claim_call_count = 0

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> ClaimedJob | None:
        async with self._lock:
            self.claim_call_count += 1
            if self.claim_call_count == 2:
                self._both_consumers_ready.set()

        async with asyncio.timeout(5):
            await self._both_consumers_ready.wait()

        return await self._delegate.claim(
            job_uuid,
            worker_id,
            lease_expires_at,
            expected_attempt_count,
        )

    async def renew_lease(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> bool:
        return await self._delegate.renew_lease(
            job_uuid,
            worker_id,
            lease_expires_at,
            expected_attempt_count,
        )

    async def is_processing_attempt(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
    ) -> bool:
        return await self._delegate.is_processing_attempt(
            job_uuid,
            expected_attempt_count,
        )


@pytest.mark.parametrize(
    ("job_type", "stream_name", "worker_id"),
    [
        (JobType.FAST, FAST_STREAM, "worker-fast-1"),
        (JobType.BATCH, BATCH_STREAM, "worker-batch-1"),
    ],
)
async def test_process_next_commits_completion_before_acknowledging(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
    job_type: JobType,
    stream_name: str,
    worker_id: str,
) -> None:
    await persist_job(async_session_factory, job_type=job_type)
    output = TranscriptionOutput(result={"text": "bonjour"})
    transcriber = FakeTranscriber(output)
    runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=job_type,
        ),
        transcriber=transcriber,
        worker_id=worker_id,
        session_factory=async_session_factory,
        job_type=job_type,
    )
    await runtime.initialize()
    redis_message_id = await worker_redis.streams.publish(
        JobStreamMessage(
            job_uuid=JOB_UUID,
            job_type=job_type,
            attempt_count=0,
        )
    )

    result = await runtime.process_next(block_milliseconds=100)

    assert isinstance(result, WorkerCompleted)
    assert result.message.redis_message_id == redis_message_id
    assert result.job == ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=job_type,
        attempt_count=0,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )
    assert result.output == output
    assert result.removed_from_stream is True
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)

    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.COMPLETED
    assert saved_job.lease_owner == worker_id
    assert saved_job.lease_expires_at == NOW + LEASE_DURATION + CLOCK_STEP
    assert saved_job.started_at is not None
    saved_result = await load_result(async_session_factory)
    assert saved_result is not None
    assert saved_result.result == {"text": "bonjour"}

    pending = await worker_redis.client.xpending(stream_name, GROUP_NAME)
    assert pending["pending"] == 0
    assert await worker_redis.client.xrange(stream_name) == []


async def test_duplicate_messages_allow_only_one_concurrent_claim_and_transcription(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    await persist_job(async_session_factory)
    synchronized_store = SynchronizedClaimStore(
        PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=JobType.FAST,
        )
    )
    transcriber = FakeTranscriber()
    first_runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=synchronized_store,
        transcriber=transcriber,
        worker_id=FIRST_WORKER_ID,
        session_factory=async_session_factory,
    )
    second_runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=synchronized_store,
        transcriber=transcriber,
        worker_id=SECOND_WORKER_ID,
        session_factory=async_session_factory,
    )
    await first_runtime.initialize()
    await second_runtime.initialize()
    message = JobStreamMessage(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=0,
    )
    published_message_ids = {
        await worker_redis.streams.publish(message),
        await worker_redis.streams.publish(message),
    }

    first_result, second_result = await asyncio.gather(
        first_runtime.process_next(block_milliseconds=100),
        second_runtime.process_next(block_milliseconds=100),
    )

    results = (first_result, second_result)
    transcribed_results = [
        result for result in results if isinstance(result, WorkerCompleted)
    ]
    rejected_results = [
        result for result in results if isinstance(result, WorkerClaimRejected)
    ]
    assert synchronized_store.claim_call_count == 2
    assert len(transcribed_results) == 1
    assert len(rejected_results) == 1
    transcribed = transcribed_results[0]
    rejected = rejected_results[0]
    assert rejected.removed_from_stream is True
    assert {
        transcribed.message.redis_message_id,
        rejected.message.redis_message_id,
    } == published_message_ids
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)

    winning_worker_id = (
        FIRST_WORKER_ID
        if isinstance(first_result, WorkerCompleted)
        else SECOND_WORKER_ID
    )
    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.COMPLETED
    assert saved_job.lease_owner == winning_worker_id
    assert saved_job.lease_expires_at == NOW + LEASE_DURATION + CLOCK_STEP
    assert saved_job.attempt_count == 0

    assert transcribed.removed_from_stream is True
    saved_result = await load_result(async_session_factory)
    assert saved_result is not None
    assert saved_result.result == {"text": "fake transcription"}
    pending = await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME)
    assert pending["pending"] == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []


async def test_job_from_another_type_is_not_processed_by_the_wrong_stream(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    job = make_job()
    job.job_type = JobType.BATCH
    async with async_transaction(async_session_factory) as session:
        await JobRepository(session).add(job)

    transcriber = FakeTranscriber()
    runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=JobType.FAST,
        ),
        transcriber=transcriber,
        worker_id=FIRST_WORKER_ID,
        session_factory=async_session_factory,
    )
    await runtime.initialize()
    redis_message_id = await worker_redis.streams.publish(
        JobStreamMessage(
            job_uuid=JOB_UUID,
            job_type=JobType.FAST,
            attempt_count=0,
        )
    )

    with pytest.raises(WorkerJobTypeMismatchError):
        await runtime.process_next(block_milliseconds=100)

    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.QUEUED
    assert saved_job.lease_owner is None
    assert saved_job.lease_expires_at is None
    assert transcriber.calls == ()
    pending = await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME)
    assert pending["pending"] == 1
    assert pending["min"] == redis_message_id


async def put_message_in_pending(
    worker_redis: WorkerRedisContext,
    *,
    job_type: JobType,
    consumer_name: str,
    attempt_count: int = 0,
) -> str:
    await worker_redis.streams.ensure_consumer_group(job_type, GROUP_NAME)
    redis_message_id = await worker_redis.streams.publish(
        JobStreamMessage(
            job_uuid=JOB_UUID,
            job_type=job_type,
            attempt_count=attempt_count,
        )
    )
    received = await worker_redis.streams.consume(
        job_type,
        GROUP_NAME,
        consumer_name,
        block_milliseconds=100,
    )
    assert received is not None
    assert received.redis_message_id == redis_message_id
    return redis_message_id


async def set_pending_idle(
    worker_redis: WorkerRedisContext,
    *,
    stream_name: str,
    consumer_name: str,
    redis_message_id: str,
    idle_milliseconds: int,
) -> None:
    claimed_ids = await worker_redis.client.xclaim(
        stream_name,
        GROUP_NAME,
        consumer_name,
        min_idle_time=0,
        message_ids=[redis_message_id],
        idle=idle_milliseconds,
        justid=True,
    )
    assert claimed_ids == [redis_message_id]


async def test_pending_message_is_reclaimed_only_after_the_configured_idle_time(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    abandoned_consumer = "worker-fast-abandoned"
    recovery_worker = "worker-fast-recovery"
    min_idle_milliseconds = 24 * 60 * 60 * 1_000
    redis_message_id = await put_message_in_pending(
        worker_redis,
        job_type=JobType.FAST,
        consumer_name=abandoned_consumer,
    )
    transcriber = FakeTranscriber()
    runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=JobType.FAST,
        ),
        transcriber=transcriber,
        worker_id=recovery_worker,
        session_factory=async_session_factory,
    )

    too_recent = await runtime.process_next_pending(
        min_idle_milliseconds=min_idle_milliseconds,
    )

    assert too_recent == WorkerIdle()
    assert (await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME))["pending"] == 1
    pending_entries = await worker_redis.client.xpending_range(
        FAST_STREAM,
        GROUP_NAME,
        min="-",
        max="+",
        count=10,
    )
    assert len(pending_entries) == 1
    assert pending_entries[0]["consumer"] == abandoned_consumer
    await set_pending_idle(
        worker_redis,
        stream_name=FAST_STREAM,
        consumer_name=abandoned_consumer,
        redis_message_id=redis_message_id,
        idle_milliseconds=min_idle_milliseconds + 1,
    )

    recovered = await runtime.process_next_pending(
        min_idle_milliseconds=min_idle_milliseconds,
    )

    # Aucun job PostgreSQL ne correspond : le message est un orphelin nettoyable.
    assert isinstance(recovered, WorkerClaimRejected)
    assert recovered.message.redis_message_id == redis_message_id
    assert recovered.removed_from_stream is True
    assert transcriber.calls == ()
    assert (await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME))["pending"] == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []


@pytest.mark.parametrize(
    "lease_expires_at",
    [
        NOW + timedelta(hours=1),
        datetime(2000, 1, 1, tzinfo=UTC),
    ],
    ids=["valid-lease", "expired-lease-awaiting-dispatcher-recovery"],
)
async def test_reclaimed_processing_attempt_is_not_inferred_or_acknowledged(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
    lease_expires_at: datetime,
) -> None:
    lease_owner = "worker-fast-original"
    recovery_worker = "worker-fast-recovery"
    async with async_transaction(async_session_factory) as session:
        await JobRepository(session).add(
            TranscriptionJob(
                job_uuid=JOB_UUID,
                status=JobStatus.PROCESSING,
                job_type=JobType.FAST,
                audio_uri=f"{JOB_UUID}/input.wav",
                dispatch_required=False,
                attempt_count=0,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
            )
        )
    redis_message_id = await put_message_in_pending(
        worker_redis,
        job_type=JobType.FAST,
        consumer_name=lease_owner,
    )
    transcriber = FakeTranscriber()
    runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=JobType.FAST,
        ),
        transcriber=transcriber,
        worker_id=recovery_worker,
        session_factory=async_session_factory,
    )

    recovered = await runtime.process_next_pending(min_idle_milliseconds=0)

    assert isinstance(recovered, WorkerClaimDeferred)
    assert recovered.message.redis_message_id == redis_message_id
    assert transcriber.calls == ()
    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.attempt_count == 0
    assert saved_job.lease_owner == lease_owner
    assert saved_job.lease_expires_at == lease_expires_at
    pending_entries = await worker_redis.client.xpending_range(
        FAST_STREAM,
        GROUP_NAME,
        min="-",
        max="+",
        count=10,
    )
    assert len(pending_entries) == 1
    assert pending_entries[0]["message_id"] == redis_message_id
    assert pending_entries[0]["consumer"] == recovery_worker
    assert await worker_redis.client.xrange(FAST_STREAM) != []


async def test_reclaimed_failed_job_is_cleaned_without_inference(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    abandoned_consumer = "worker-fast-abandoned"
    async with async_transaction(async_session_factory) as session:
        await JobRepository(session).add(
            TranscriptionJob(
                job_uuid=JOB_UUID,
                status=JobStatus.FAILED,
                job_type=JobType.FAST,
                audio_uri=f"{JOB_UUID}/input.wav",
                dispatch_required=False,
                attempt_count=0,
                last_error="PERMANENT_TRANSCRIPTION_FAILURE",
                completed_at=NOW,
            )
        )
    redis_message_id = await put_message_in_pending(
        worker_redis,
        job_type=JobType.FAST,
        consumer_name=abandoned_consumer,
    )
    transcriber = FakeTranscriber()
    runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=JobType.FAST,
        ),
        transcriber=transcriber,
        worker_id="worker-fast-recovery",
        session_factory=async_session_factory,
    )

    recovered = await runtime.process_next_pending(min_idle_milliseconds=0)

    assert isinstance(recovered, WorkerClaimRejected)
    assert recovered.message.redis_message_id == redis_message_id
    assert recovered.removed_from_stream is True
    assert transcriber.calls == ()
    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.FAILED
    assert saved_job.attempt_count == 0
    assert saved_job.last_error == "PERMANENT_TRANSCRIPTION_FAILURE"
    assert (await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME))["pending"] == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []


@pytest.mark.parametrize(
    ("job_type", "stream_name", "abandoned_consumer", "recovery_worker"),
    [
        (
            JobType.FAST,
            FAST_STREAM,
            "worker-fast-abandoned",
            "worker-fast-recovery",
        ),
        (
            JobType.BATCH,
            BATCH_STREAM,
            "worker-batch-abandoned",
            "worker-batch-recovery",
        ),
    ],
)
async def test_reclaimed_queued_attempt_uses_the_normal_claim_path(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
    job_type: JobType,
    stream_name: str,
    abandoned_consumer: str,
    recovery_worker: str,
) -> None:
    await persist_job(async_session_factory, job_type=job_type)
    redis_message_id = await put_message_in_pending(
        worker_redis,
        job_type=job_type,
        consumer_name=abandoned_consumer,
    )
    output = TranscriptionOutput(result={"text": f"recovered {job_type.value}"})
    transcriber = FakeTranscriber(output)
    runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=job_type,
        ),
        transcriber=transcriber,
        worker_id=recovery_worker,
        session_factory=async_session_factory,
        job_type=job_type,
    )

    recovered = await runtime.process_next_pending(min_idle_milliseconds=0)

    assert isinstance(recovered, WorkerCompleted)
    assert recovered.message.redis_message_id == redis_message_id
    assert recovered.job.job_type is job_type
    assert recovered.removed_from_stream is True
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)
    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.COMPLETED
    assert saved_job.attempt_count == 0
    assert saved_job.lease_owner == recovery_worker
    saved_result = await load_result(async_session_factory)
    assert saved_result is not None
    assert saved_result.result == output.result
    assert (await worker_redis.client.xpending(stream_name, GROUP_NAME))["pending"] == 0
    assert await worker_redis.client.xrange(stream_name) == []
