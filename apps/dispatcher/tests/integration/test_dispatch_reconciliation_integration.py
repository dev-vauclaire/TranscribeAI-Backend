from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import pytest
import redis.asyncio as redis_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dispatcher.models import ReconciliationBatchResult
from dispatcher.postgresql import PostgresDispatchJobStore
from dispatcher.reconciliation import DispatchReconciliationService
from dispatcher.service import DispatcherService
from transcribe_ai_shared import (
    AudioLocation,
    JobRepository,
    JobStatus,
    JobStreamMessage,
    JobType,
    PostgresWorkerJobStore,
    RedisTranscriptionStreams,
    TranscriptionCompletionService,
    TranscriptionFailureService,
    TranscriptionJob,
    WorkerClaimRejected,
    WorkerCompleted,
    WorkerRuntime,
    async_transaction,
)
from transcribe_ai_shared.worker.testing import FakeTranscriber


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAST_STREAM = "transcription:fast"
RECONCILIATION_TIMEOUT_SECONDS = 300


class DispatcherRedisContext(Protocol):
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


def make_job(
    job_uuid: UUID,
    *,
    status: JobStatus = JobStatus.QUEUED,
    job_type: JobType = JobType.FAST,
    attempt_count: int = 0,
    dispatch_required: bool = False,
    last_dispatched_at: datetime,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=job_uuid,
        status=status,
        job_type=job_type,
        audio_uri=f"{job_uuid}/input.wav",
        attempt_count=attempt_count,
        dispatch_required=dispatch_required,
        last_dispatched_at=last_dispatched_at,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )


async def persist_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    *jobs: TranscriptionJob,
) -> None:
    async with async_transaction(session_factory) as session:
        repository = JobRepository(session)
        for job in jobs:
            await repository.add(job)


async def load_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    *job_uuids: UUID,
) -> dict[UUID, TranscriptionJob]:
    async with session_factory() as session:
        jobs = await JobRepository(session).get_jobs_by_uuids(job_uuids)
    return {job.job_uuid: job for job in jobs}


async def test_reconciliation_rearms_only_stale_queued_jobs(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    current_time = datetime.now(UTC)
    old_dispatch = current_time - timedelta(hours=1)
    recent_dispatch = current_time - timedelta(seconds=30)
    stale_queued_uuid = UUID("30000000-0000-0000-0000-000000000001")
    recent_queued_uuid = UUID("30000000-0000-0000-0000-000000000002")
    processing_uuid = UUID("30000000-0000-0000-0000-000000000003")
    completed_uuid = UUID("30000000-0000-0000-0000-000000000004")
    failed_uuid = UUID("30000000-0000-0000-0000-000000000005")
    await persist_jobs(
        async_session_factory,
        make_job(
            stale_queued_uuid,
            attempt_count=3,
            last_dispatched_at=old_dispatch,
        ),
        make_job(recent_queued_uuid, last_dispatched_at=recent_dispatch),
        make_job(
            processing_uuid,
            status=JobStatus.PROCESSING,
            last_dispatched_at=old_dispatch,
            lease_owner="worker-fast-1",
            lease_expires_at=current_time + timedelta(hours=1),
        ),
        make_job(
            completed_uuid,
            status=JobStatus.COMPLETED,
            last_dispatched_at=old_dispatch,
        ),
        make_job(
            failed_uuid,
            status=JobStatus.FAILED,
            last_dispatched_at=old_dispatch,
        ),
    )
    store = PostgresDispatchJobStore(async_session_factory)

    result = await DispatchReconciliationService(job_store=store).reconcile_batch(
        batch_size=10,
        reconciliation_timeout_seconds=RECONCILIATION_TIMEOUT_SECONDS,
    )

    assert result == ReconciliationBatchResult(1, 1, 0, 0)
    saved = await load_jobs(
        async_session_factory,
        stale_queued_uuid,
        recent_queued_uuid,
        processing_uuid,
        completed_uuid,
        failed_uuid,
    )
    assert saved[stale_queued_uuid].dispatch_required is True
    assert saved[stale_queued_uuid].attempt_count == 3
    assert saved[stale_queued_uuid].last_dispatched_at == old_dispatch
    assert saved[recent_queued_uuid].dispatch_required is False
    assert saved[processing_uuid].dispatch_required is False
    assert saved[completed_uuid].dispatch_required is False
    assert saved[failed_uuid].dispatch_required is False


async def test_reconciliation_cas_does_not_overwrite_a_newer_dispatch(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    old_dispatch = datetime.now(UTC) - timedelta(hours=1)
    newer_dispatch = datetime.now(UTC)
    job_uuid = UUID("40000000-0000-0000-0000-000000000001")
    await persist_jobs(
        async_session_factory,
        make_job(job_uuid, attempt_count=2, last_dispatched_at=old_dispatch),
    )
    store = PostgresDispatchJobStore(async_session_factory)
    snapshots = await store.find_stale_dispatched_jobs(
        limit=1,
        reconciliation_timeout_seconds=RECONCILIATION_TIMEOUT_SECONDS,
    )
    assert len(snapshots) == 1

    async with async_session_factory.begin() as session:
        await session.execute(
            update(TranscriptionJob)
            .where(TranscriptionJob.job_uuid == job_uuid)
            .values(last_dispatched_at=newer_dispatch)
        )

    rearmed = await store.rearm_stale_dispatch(
        snapshots[0],
        RECONCILIATION_TIMEOUT_SECONDS,
    )

    saved = await load_jobs(async_session_factory, job_uuid)
    assert rearmed is False
    assert saved[job_uuid].dispatch_required is False
    assert saved[job_uuid].attempt_count == 2
    assert saved[job_uuid].last_dispatched_at == newer_dispatch


async def test_reconciliation_duplicate_allows_only_one_inference(
    async_session_factory: async_sessionmaker[AsyncSession],
    dispatcher_redis: DispatcherRedisContext,
) -> None:
    old_dispatch = datetime.now(UTC) - timedelta(hours=1)
    job_uuid = UUID("50000000-0000-0000-0000-000000000001")
    attempt_count = 2
    await persist_jobs(
        async_session_factory,
        make_job(
            job_uuid,
            attempt_count=attempt_count,
            last_dispatched_at=old_dispatch,
        ),
    )
    original_message = JobStreamMessage(
        job_uuid=job_uuid,
        job_type=JobType.FAST,
        attempt_count=attempt_count,
    )
    first_message_id = await dispatcher_redis.streams.publish(original_message)
    store = PostgresDispatchJobStore(async_session_factory)

    reconciliation = await DispatchReconciliationService(
        job_store=store
    ).reconcile_batch(
        batch_size=10,
        reconciliation_timeout_seconds=RECONCILIATION_TIMEOUT_SECONDS,
    )
    dispatch = await DispatcherService(
        job_store=store,
        streams=dispatcher_redis.streams,
    ).dispatch_batch(10)

    assert reconciliation == ReconciliationBatchResult(1, 1, 0, 0)
    assert dispatch.selected_count == 1
    assert dispatch.published_count == 1
    assert dispatch.confirmed_count == 1
    entries = await dispatcher_redis.client.xrange(FAST_STREAM)
    assert len(entries) == 2
    assert first_message_id == entries[0][0]
    assert entries[0][0] != entries[1][0]
    assert [payload for _, payload in entries] == [
        {"job_uuid": str(job_uuid), "attempt_count": str(attempt_count)},
        {"job_uuid": str(job_uuid), "attempt_count": str(attempt_count)},
    ]
    saved = await load_jobs(async_session_factory, job_uuid)
    assert saved[job_uuid].dispatch_required is False
    assert saved[job_uuid].attempt_count == attempt_count
    assert saved[job_uuid].last_dispatched_at is not None
    assert saved[job_uuid].last_dispatched_at > old_dispatch

    transcriber = FakeTranscriber()
    runtime = WorkerRuntime(
        streams=dispatcher_redis.streams,
        job_store=PostgresWorkerJobStore(
            async_session_factory,
            expected_job_type=JobType.FAST,
        ),
        transcriber=transcriber,
        completer=TranscriptionCompletionService(async_session_factory),
        failure_handler=TranscriptionFailureService(
            async_session_factory,
            max_attempts=3,
        ),
        job_type=JobType.FAST,
        group_name="reconciliation-workers",
        worker_id="worker-fast-reconciliation-1",
        lease_duration=timedelta(minutes=5),
    )
    await runtime.initialize()

    first_result = await runtime.process_next(block_milliseconds=100)
    second_result = await runtime.process_next(block_milliseconds=100)

    assert isinstance(first_result, WorkerCompleted)
    assert isinstance(second_result, WorkerClaimRejected)
    assert second_result.removed_from_stream is True
    assert transcriber.calls == (AudioLocation(f"{job_uuid}/input.wav"),)
    completed = await load_jobs(async_session_factory, job_uuid)
    assert completed[job_uuid].status is JobStatus.COMPLETED
    assert completed[job_uuid].attempt_count == attempt_count
    pending = await dispatcher_redis.client.xpending(
        FAST_STREAM,
        "reconciliation-workers",
    )
    assert pending["pending"] == 0
    assert await dispatcher_redis.client.xrange(FAST_STREAM) == []


async def test_reconciliation_rebuilds_a_message_after_the_redis_stream_is_lost(
    async_session_factory: async_sessionmaker[AsyncSession],
    dispatcher_redis: DispatcherRedisContext,
) -> None:
    old_dispatch = datetime.now(UTC) - timedelta(hours=1)
    job_uuid = UUID("60000000-0000-0000-0000-000000000001")
    attempt_count = 2
    await persist_jobs(
        async_session_factory,
        make_job(
            job_uuid,
            attempt_count=attempt_count,
            last_dispatched_at=old_dispatch,
        ),
    )
    await dispatcher_redis.streams.publish(
        JobStreamMessage(
            job_uuid=job_uuid,
            job_type=JobType.FAST,
            attempt_count=attempt_count,
        )
    )
    assert await dispatcher_redis.client.delete(FAST_STREAM) == 1
    assert await dispatcher_redis.client.exists(FAST_STREAM) == 0
    store = PostgresDispatchJobStore(async_session_factory)

    reconciliation = await DispatchReconciliationService(
        job_store=store
    ).reconcile_batch(
        batch_size=10,
        reconciliation_timeout_seconds=RECONCILIATION_TIMEOUT_SECONDS,
    )
    dispatch = await DispatcherService(
        job_store=store,
        streams=dispatcher_redis.streams,
    ).dispatch_batch(10)

    assert reconciliation == ReconciliationBatchResult(1, 1, 0, 0)
    assert dispatch.selected_count == 1
    assert dispatch.published_count == 1
    assert dispatch.confirmed_count == 1
    assert [
        payload for _, payload in await dispatcher_redis.client.xrange(FAST_STREAM)
    ] == [
        {
            "job_uuid": str(job_uuid),
            "attempt_count": str(attempt_count),
        }
    ]
    saved = await load_jobs(async_session_factory, job_uuid)
    assert saved[job_uuid].dispatch_required is False
    assert saved[job_uuid].attempt_count == attempt_count
    assert saved[job_uuid].last_dispatched_at is not None
    assert saved[job_uuid].last_dispatched_at > old_dispatch
