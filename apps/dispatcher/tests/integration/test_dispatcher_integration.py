from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import pytest
import redis.asyncio as redis_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dispatcher.postgresql import PostgresDispatchJobStore
from dispatcher.service import DispatcherService
from transcribe_ai_shared import (
    JobRepository,
    JobStatus,
    JobStreamMessage,
    JobType,
    RedisTranscriptionStreams,
    TranscriptionJob,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAST_STREAM = "transcription:fast"
BATCH_STREAM = "transcription:batch"
NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)


class DispatcherRedisContext(Protocol):
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


def make_job(
    job_uuid: UUID,
    job_type: JobType,
    *,
    attempt_count: int = 0,
    status: JobStatus = JobStatus.QUEUED,
    dispatch_required: bool = True,
    created_at: datetime = NOW,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=job_uuid,
        status=status,
        job_type=job_type,
        audio_uri=f"{job_uuid}/input.wav",
        dispatch_required=dispatch_required,
        attempt_count=attempt_count,
        created_at=created_at,
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


class RefusingMarkStore:
    """Simule un CAS perdu après une publication Redis réussie."""

    def __init__(self, delegate: PostgresDispatchJobStore) -> None:
        self._delegate = delegate
        self.mark_calls: list[tuple[UUID, int, datetime]] = []

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[JobStreamMessage]:
        return await self._delegate.find_jobs_requiring_dispatch(limit)

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        dispatched_at: datetime,
    ) -> bool:
        self.mark_calls.append(
            (job_uuid, expected_attempt_count, dispatched_at),
        )
        return False


class RequeueBeforeMarkStore:
    """Reproduit une requeue entre XADD et le CAS de l'ancien dispatcher."""

    def __init__(
        self,
        delegate: PostgresDispatchJobStore,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._delegate = delegate
        self._session_factory = session_factory
        self.observed_attempt_count: int | None = None

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[JobStreamMessage]:
        return await self._delegate.find_jobs_requiring_dispatch(limit)

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        dispatched_at: datetime,
    ) -> bool:
        self.observed_attempt_count = expected_attempt_count

        async with self._session_factory.begin() as session:
            # Le worker a claim le message publié, puis son lease a expiré.
            await session.execute(
                update(TranscriptionJob)
                .where(TranscriptionJob.job_uuid == job_uuid)
                .values(
                    status=JobStatus.PROCESSING,
                    lease_owner="expired-worker",
                    lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC),
                )
            )
            requeued = await JobRepository(session).requeue_expired_job(job_uuid)
            assert requeued is not None
            assert requeued.attempt_count == expected_attempt_count + 1

        return await self._delegate.mark_dispatched(
            job_uuid,
            expected_attempt_count,
            dispatched_at,
        )


async def test_dispatch_batch_publishes_only_eligible_jobs_to_matching_stream(
    async_session_factory: async_sessionmaker[AsyncSession],
    dispatcher_redis: DispatcherRedisContext,
) -> None:
    first_fast_uuid = UUID("00000000-0000-0000-0000-000000000001")
    batch_uuid = UUID("00000000-0000-0000-0000-000000000002")
    second_fast_uuid = UUID("00000000-0000-0000-0000-000000000003")
    already_dispatched_uuid = UUID("00000000-0000-0000-0000-000000000004")
    processing_uuid = UUID("00000000-0000-0000-0000-000000000005")
    await persist_jobs(
        async_session_factory,
        make_job(
            first_fast_uuid,
            JobType.FAST,
            created_at=NOW - timedelta(minutes=5),
        ),
        make_job(
            batch_uuid,
            JobType.BATCH,
            attempt_count=2,
            created_at=NOW - timedelta(minutes=4),
        ),
        make_job(
            second_fast_uuid,
            JobType.FAST,
            attempt_count=1,
            created_at=NOW - timedelta(minutes=3),
        ),
        make_job(
            already_dispatched_uuid,
            JobType.FAST,
            dispatch_required=False,
            created_at=NOW - timedelta(minutes=10),
        ),
        make_job(
            processing_uuid,
            JobType.BATCH,
            status=JobStatus.PROCESSING,
            lease_owner="batch-worker",
            lease_expires_at=NOW + timedelta(minutes=5),
            created_at=NOW - timedelta(minutes=10),
        ),
    )
    store = PostgresDispatchJobStore(async_session_factory)
    service = DispatcherService(
        job_store=store,
        streams=dispatcher_redis.streams,
        clock=lambda: NOW,
    )

    await service.dispatch_batch(10)

    fast_entries = await dispatcher_redis.client.xrange(FAST_STREAM)
    batch_entries = await dispatcher_redis.client.xrange(BATCH_STREAM)
    assert [payload for _, payload in fast_entries] == [
        {"job_uuid": str(first_fast_uuid), "attempt_count": "0"},
        {"job_uuid": str(second_fast_uuid), "attempt_count": "1"},
    ]
    assert [payload for _, payload in batch_entries] == [
        {"job_uuid": str(batch_uuid), "attempt_count": "2"},
    ]

    saved = await load_jobs(
        async_session_factory,
        first_fast_uuid,
        batch_uuid,
        second_fast_uuid,
        already_dispatched_uuid,
        processing_uuid,
    )
    for job_uuid in (first_fast_uuid, batch_uuid, second_fast_uuid):
        assert saved[job_uuid].dispatch_required is False
        assert saved[job_uuid].last_dispatched_at == NOW

    assert saved[already_dispatched_uuid].dispatch_required is False
    assert saved[already_dispatched_uuid].last_dispatched_at is None
    assert saved[processing_uuid].status is JobStatus.PROCESSING
    assert saved[processing_uuid].dispatch_required is True
    assert saved[processing_uuid].last_dispatched_at is None


async def test_second_run_republishes_when_first_mark_did_not_match(
    async_session_factory: async_sessionmaker[AsyncSession],
    dispatcher_redis: DispatcherRedisContext,
) -> None:
    job_uuid = UUID("10000000-0000-0000-0000-000000000001")
    await persist_jobs(
        async_session_factory,
        make_job(job_uuid, JobType.FAST),
    )
    real_store = PostgresDispatchJobStore(async_session_factory)
    refusing_store = RefusingMarkStore(real_store)

    await DispatcherService(
        job_store=refusing_store,
        streams=dispatcher_redis.streams,
        clock=lambda: NOW,
    ).dispatch_batch(10)

    after_first_run = await load_jobs(async_session_factory, job_uuid)
    assert after_first_run[job_uuid].dispatch_required is True
    assert after_first_run[job_uuid].last_dispatched_at is None
    assert refusing_store.mark_calls == [(job_uuid, 0, NOW)]
    first_entries = await dispatcher_redis.client.xrange(FAST_STREAM)
    assert [payload for _, payload in first_entries] == [
        {"job_uuid": str(job_uuid), "attempt_count": "0"},
    ]

    await DispatcherService(
        job_store=real_store,
        streams=dispatcher_redis.streams,
        clock=lambda: NOW,
    ).dispatch_batch(10)

    entries_after_second_run = await dispatcher_redis.client.xrange(FAST_STREAM)
    assert len(entries_after_second_run) == 2
    assert entries_after_second_run[0][0] != entries_after_second_run[1][0]
    assert [payload for _, payload in entries_after_second_run] == [
        {"job_uuid": str(job_uuid), "attempt_count": "0"},
        {"job_uuid": str(job_uuid), "attempt_count": "0"},
    ]
    saved = await load_jobs(async_session_factory, job_uuid)
    assert saved[job_uuid].dispatch_required is False
    assert saved[job_uuid].last_dispatched_at == NOW


async def test_stale_attempt_cannot_clear_dispatch_required_after_requeue(
    async_session_factory: async_sessionmaker[AsyncSession],
    dispatcher_redis: DispatcherRedisContext,
) -> None:
    job_uuid = UUID("20000000-0000-0000-0000-000000000001")
    await persist_jobs(
        async_session_factory,
        make_job(job_uuid, JobType.BATCH, attempt_count=0),
    )
    stale_store = RequeueBeforeMarkStore(
        PostgresDispatchJobStore(async_session_factory),
        async_session_factory,
    )

    await DispatcherService(
        job_store=stale_store,
        streams=dispatcher_redis.streams,
        clock=lambda: NOW,
    ).dispatch_batch(10)

    assert stale_store.observed_attempt_count == 0
    assert [
        payload for _, payload in await dispatcher_redis.client.xrange(BATCH_STREAM)
    ] == [{"job_uuid": str(job_uuid), "attempt_count": "0"}]
    saved = await load_jobs(async_session_factory, job_uuid)
    assert saved[job_uuid].status is JobStatus.QUEUED
    assert saved[job_uuid].attempt_count == 1
    assert saved[job_uuid].dispatch_required is True
    assert saved[job_uuid].last_dispatched_at is None
    assert saved[job_uuid].lease_owner is None
    assert saved[job_uuid].lease_expires_at is None
