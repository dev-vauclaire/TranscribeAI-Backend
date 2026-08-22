from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dispatcher.models import ExpiredJobSnapshot, RecoveryBatchResult
from dispatcher.postgresql import PostgresDispatchJobStore
from dispatcher.recovery import LeaseRecoveryService
from transcribe_ai_shared import (
    JobRepository,
    JobStatus,
    JobType,
    TranscriptionJob,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EXPIRED_LEASE = datetime(2000, 1, 1, tzinfo=UTC)
RENEWED_LEASE = datetime(2099, 1, 1, tzinfo=UTC)
WORKER_ID = "worker-fast-1"


def make_processing_job(
    job_uuid: UUID,
    *,
    attempt_count: int,
    lease_expires_at: datetime,
) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=job_uuid,
        status=JobStatus.PROCESSING,
        job_type=JobType.FAST,
        audio_uri=f"{job_uuid}/input.wav",
        dispatch_required=False,
        attempt_count=attempt_count,
        lease_owner=WORKER_ID,
        lease_expires_at=lease_expires_at,
    )


async def persist_job(
    session_factory: async_sessionmaker[AsyncSession],
    job: TranscriptionJob,
) -> None:
    async with async_transaction(session_factory) as session:
        await JobRepository(session).add(job)


async def load_job(
    session_factory: async_sessionmaker[AsyncSession],
    job_uuid: UUID,
) -> TranscriptionJob:
    async with session_factory() as session:
        job = await JobRepository(session).get_by_uuid(job_uuid)
        assert job is not None
        return job


def make_service(job_store: PostgresDispatchJobStore) -> LeaseRecoveryService:
    return LeaseRecoveryService(job_store=job_store)


async def test_recovery_ignores_a_future_lease(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = UUID("30000000-0000-0000-0000-000000000001")
    await persist_job(
        async_session_factory,
        make_processing_job(
            job_uuid,
            attempt_count=0,
            lease_expires_at=RENEWED_LEASE,
        ),
    )

    result = await make_service(
        PostgresDispatchJobStore(async_session_factory)
    ).recover_batch(batch_size=10, max_attempts=3)

    saved = await load_job(async_session_factory, job_uuid)
    assert result == RecoveryBatchResult(0, 0, 0, 0, 0)
    assert saved.status is JobStatus.PROCESSING
    assert saved.attempt_count == 0
    assert saved.dispatch_required is False
    assert saved.lease_owner == WORKER_ID
    assert saved.lease_expires_at == RENEWED_LEASE


async def test_recovery_requeues_an_expired_lease_and_increments_the_attempt(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = UUID("30000000-0000-0000-0000-000000000002")
    await persist_job(
        async_session_factory,
        make_processing_job(
            job_uuid,
            attempt_count=1,
            lease_expires_at=EXPIRED_LEASE,
        ),
    )

    result = await make_service(
        PostgresDispatchJobStore(async_session_factory)
    ).recover_batch(batch_size=10, max_attempts=3)

    saved = await load_job(async_session_factory, job_uuid)
    assert result == RecoveryBatchResult(1, 1, 0, 0, 0)
    assert saved.status is JobStatus.QUEUED
    assert saved.attempt_count == 2
    assert saved.dispatch_required is True
    assert saved.lease_owner is None
    assert saved.lease_expires_at is None
    assert saved.last_error == "WORKER_LEASE_EXPIRED"
    assert saved.completed_at is None


async def test_recovery_marks_the_last_expired_attempt_as_failed(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = UUID("30000000-0000-0000-0000-000000000003")
    await persist_job(
        async_session_factory,
        make_processing_job(
            job_uuid,
            attempt_count=2,
            lease_expires_at=EXPIRED_LEASE,
        ),
    )

    result = await make_service(
        PostgresDispatchJobStore(async_session_factory)
    ).recover_batch(batch_size=10, max_attempts=3)

    saved = await load_job(async_session_factory, job_uuid)
    assert result == RecoveryBatchResult(1, 0, 1, 0, 0)
    assert saved.status is JobStatus.FAILED
    assert saved.attempt_count == 2
    assert saved.dispatch_required is False
    assert saved.lease_owner is None
    assert saved.lease_expires_at is None
    assert saved.last_error == "WORKER_LEASE_EXPIRED"
    assert saved.completed_at is not None


class RenewLeaseBeforeRecoveryStore:
    """Simule le commit d'un heartbeat entre la sélection et le CAS recovery."""

    def __init__(
        self,
        delegate: PostgresDispatchJobStore,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._delegate = delegate
        self._session_factory = session_factory

    async def find_expired_jobs(
        self,
        limit: int,
    ) -> list[ExpiredJobSnapshot]:
        return await self._delegate.find_expired_jobs(limit)

    async def recover_expired_job(
        self,
        snapshot: ExpiredJobSnapshot,
        *,
        should_retry: bool,
    ) -> bool:
        async with async_transaction(self._session_factory) as session:
            await session.execute(
                update(TranscriptionJob)
                .where(TranscriptionJob.job_uuid == snapshot.job_uuid)
                .values(lease_expires_at=RENEWED_LEASE)
            )
        return await self._delegate.recover_expired_job(
            snapshot,
            should_retry=should_retry,
        )


async def test_recovery_does_not_overwrite_a_lease_renewed_after_selection(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = UUID("30000000-0000-0000-0000-000000000004")
    await persist_job(
        async_session_factory,
        make_processing_job(
            job_uuid,
            attempt_count=0,
            lease_expires_at=EXPIRED_LEASE,
        ),
    )
    real_store = PostgresDispatchJobStore(async_session_factory)
    service = LeaseRecoveryService(
        job_store=RenewLeaseBeforeRecoveryStore(
            real_store,
            async_session_factory,
        ),
    )

    result = await service.recover_batch(batch_size=10, max_attempts=3)

    saved = await load_job(async_session_factory, job_uuid)
    assert result == RecoveryBatchResult(1, 0, 0, 1, 0)
    assert saved.status is JobStatus.PROCESSING
    assert saved.attempt_count == 0
    assert saved.dispatch_required is False
    assert saved.lease_owner == WORKER_ID
    assert saved.lease_expires_at == RENEWED_LEASE
    assert saved.last_error is None
