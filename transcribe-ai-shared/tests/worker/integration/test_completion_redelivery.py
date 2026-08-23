from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from transcribe_ai_shared import (
    AudioLocation,
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
    TranscriptionStreams,
    WorkerClaimRejected,
    WorkerRuntime,
    async_transaction,
)
from transcribe_ai_shared.queue.models import ReceivedJobStreamMessage
from transcribe_ai_shared.worker.testing import FakeTranscriber

from .conftest import WorkerRedisContext


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAST_STREAM = "transcription:fast"
GROUP_NAME = "transcription-workers"
FIRST_WORKER_ID = "worker-fast-1"
RECOVERY_WORKER_ID = "worker-fast-recovery-1"
JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
NOW = datetime(2099, 1, 1, 12, tzinfo=UTC)
CLOCK_STEP = timedelta(seconds=1)
LEASE_DURATION = timedelta(minutes=5)


class AdvancingClock:
    """Fournit un lease croissant au claim puis au renouvellement final."""

    def __init__(self) -> None:
        self._current = NOW

    def __call__(self) -> datetime:
        current = self._current
        self._current += CLOCK_STEP
        return current


class SimulatedWorkerCrash(RuntimeError):
    """Interrompt le premier worker à la frontière COMMIT PostgreSQL / ACK Redis."""


class CrashBeforeAckStreams:
    """Délègue Redis jusqu'au moment où le worker devrait acquitter le message."""

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


def make_runtime(
    *,
    streams: TranscriptionStreams,
    session_factory: async_sessionmaker[AsyncSession],
    transcriber: FakeTranscriber,
    worker_id: str,
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
            max_attempts=3,
        ),
        job_type=JobType.FAST,
        group_name=GROUP_NAME,
        worker_id=worker_id,
        lease_duration=LEASE_DURATION,
        clock=AdvancingClock(),
    )


async def test_committed_completion_is_not_inferred_again_after_autoclaim(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_redis: WorkerRedisContext,
) -> None:
    async with async_transaction(async_session_factory) as session:
        await JobRepository(session).add(
            TranscriptionJob(
                job_uuid=JOB_UUID,
                status=JobStatus.QUEUED,
                job_type=JobType.FAST,
                audio_uri=f"{JOB_UUID}/input.wav",
                dispatch_required=False,
                attempt_count=0,
            )
        )

    output = TranscriptionOutput(result={"text": "résultat durable"})
    transcriber = FakeTranscriber(output)
    first_runtime = make_runtime(
        streams=CrashBeforeAckStreams(worker_redis.streams),
        session_factory=async_session_factory,
        transcriber=transcriber,
        worker_id=FIRST_WORKER_ID,
    )
    await first_runtime.initialize()
    redis_message_id = await worker_redis.streams.publish(
        JobStreamMessage(
            job_uuid=JOB_UUID,
            job_type=JobType.FAST,
            attempt_count=0,
        )
    )

    with pytest.raises(SimulatedWorkerCrash, match="before Redis ACK"):
        await first_runtime.process_next(block_milliseconds=100)

    # Le processus disparaît après le COMMIT PostgreSQL, avant tout ACK Redis.
    pending_before_recovery = await worker_redis.client.xpending(
        FAST_STREAM,
        GROUP_NAME,
    )
    assert pending_before_recovery["pending"] == 1
    assert pending_before_recovery["min"] == redis_message_id
    async with async_session_factory() as session:
        committed_job = await JobRepository(session).get_by_uuid(JOB_UUID)
        committed_result = await ResultRepository(session).get_by_job_uuid(JOB_UUID)
    assert committed_job is not None
    assert committed_job.status is JobStatus.COMPLETED
    assert committed_result is not None
    assert committed_result.result == output.result

    recovery_runtime = make_runtime(
        streams=worker_redis.streams,
        session_factory=async_session_factory,
        transcriber=transcriber,
        worker_id=RECOVERY_WORKER_ID,
    )
    # Le seuil nul rend uniquement le message Redis reclaimable immédiatement.
    # PostgreSQL COMPLETED, et non son idle time, décide de son nettoyage.
    recovered = await recovery_runtime.process_next_pending(
        min_idle_milliseconds=0,
    )

    assert isinstance(recovered, WorkerClaimRejected)
    assert recovered.message.redis_message_id == redis_message_id
    assert recovered.removed_from_stream is True
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)
    assert (await worker_redis.client.xpending(FAST_STREAM, GROUP_NAME))["pending"] == 0
    assert await worker_redis.client.xrange(FAST_STREAM) == []
