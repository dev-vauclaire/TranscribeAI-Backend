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
    TranscriptionJob,
    TranscriptionOutput,
    TranscriptionStreams,
    WorkerClaimRejected,
    WorkerJobStore,
    WorkerJobTypeMismatchError,
    WorkerRuntime,
    WorkerTranscribed,
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
NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
LEASE_DURATION = timedelta(minutes=5)


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


def make_runtime(
    *,
    streams: TranscriptionStreams,
    job_store: WorkerJobStore,
    transcriber: FakeTranscriber,
    worker_id: str,
    job_type: JobType = JobType.FAST,
) -> WorkerRuntime:
    return WorkerRuntime(
        streams=streams,
        job_store=job_store,
        transcriber=transcriber,
        job_type=job_type,
        group_name=GROUP_NAME,
        worker_id=worker_id,
        lease_duration=LEASE_DURATION,
        clock=lambda: NOW,
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
        )


@pytest.mark.parametrize(
    ("job_type", "stream_name", "worker_id"),
    [
        (JobType.FAST, FAST_STREAM, "worker-fast-1"),
        (JobType.BATCH, BATCH_STREAM, "worker-batch-1"),
    ],
)
async def test_process_next_claims_and_transcribes_without_premature_ack(
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

    assert isinstance(result, WorkerTranscribed)
    assert result.message.redis_message_id == redis_message_id
    assert result.job == ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=job_type,
        attempt_count=0,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )
    assert result.output == output
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)

    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.lease_owner == worker_id
    assert saved_job.lease_expires_at == NOW + LEASE_DURATION
    assert saved_job.started_at is not None

    pending = await worker_redis.client.xpending(stream_name, GROUP_NAME)
    assert pending["pending"] == 1
    assert pending["min"] == redis_message_id
    assert await worker_redis.client.xrange(stream_name) == [
        (
            redis_message_id,
            {
                "job_uuid": str(JOB_UUID),
                "attempt_count": "0",
            },
        )
    ]


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
    )
    second_runtime = make_runtime(
        streams=worker_redis.streams,
        job_store=synchronized_store,
        transcriber=transcriber,
        worker_id=SECOND_WORKER_ID,
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
        result for result in results if isinstance(result, WorkerTranscribed)
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
        if isinstance(first_result, WorkerTranscribed)
        else SECOND_WORKER_ID
    )
    saved_job = await load_job(async_session_factory)
    assert saved_job.status is JobStatus.PROCESSING
    assert saved_job.lease_owner == winning_worker_id
    assert saved_job.lease_expires_at == NOW + LEASE_DURATION
    assert saved_job.attempt_count == 0

    pending_entries = await worker_redis.client.xpending_range(
        FAST_STREAM,
        GROUP_NAME,
        min="-",
        max="+",
        count=10,
    )
    assert len(pending_entries) == 1
    assert pending_entries[0]["message_id"] == transcribed.message.redis_message_id
    assert pending_entries[0]["consumer"] == winning_worker_id
    assert await worker_redis.client.xrange(FAST_STREAM) == [
        (
            transcribed.message.redis_message_id,
            {
                "job_uuid": str(JOB_UUID),
                "attempt_count": "0",
            },
        )
    ]


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
