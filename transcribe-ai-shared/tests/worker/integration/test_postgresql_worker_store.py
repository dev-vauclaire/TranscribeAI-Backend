from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from transcribe_ai_shared import (
    JobRepository,
    JobStatus,
    JobType,
    PostgresWorkerJobStore,
    TranscriptionJob,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
WORKER_ID = "worker-fast-1"
NOW = datetime(2099, 1, 1, 12, tzinfo=UTC)


async def persist_processing_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lease_expires_at: datetime = NOW + timedelta(minutes=1),
    attempt_count: int = 3,
) -> None:
    async with async_transaction(session_factory) as session:
        await JobRepository(session).add(
            TranscriptionJob(
                job_uuid=JOB_UUID,
                status=JobStatus.PROCESSING,
                job_type=JobType.FAST,
                audio_uri=f"{JOB_UUID}/input.wav",
                dispatch_required=False,
                attempt_count=attempt_count,
                lease_owner=WORKER_ID,
                lease_expires_at=lease_expires_at,
            )
        )


async def load_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> TranscriptionJob:
    async with session_factory() as session:
        job = await JobRepository(session).get_by_uuid(JOB_UUID)
        assert job is not None
        return job


async def test_renew_lease_commits_the_current_owners_attempt(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory)
    renewed_expiration = NOW + timedelta(minutes=10)
    store = PostgresWorkerJobStore(
        async_session_factory,
        expected_job_type=JobType.FAST,
    )

    renewed = await store.renew_lease(
        JOB_UUID,
        WORKER_ID,
        renewed_expiration,
        3,
    )

    saved = await load_job(async_session_factory)
    assert renewed is True
    assert saved.lease_expires_at > NOW + timedelta(minutes=1)
    assert saved.lease_expires_at == renewed_expiration


@pytest.mark.parametrize(
    ("worker_id", "expected_attempt_count"),
    [
        ("worker-fast-2", 3),
        (WORKER_ID, 2),
    ],
)
async def test_renew_lease_forwards_owner_and_attempt_guards(
    async_session_factory: async_sessionmaker[AsyncSession],
    worker_id: str,
    expected_attempt_count: int,
) -> None:
    await persist_processing_job(async_session_factory)
    previous_expiration = NOW + timedelta(minutes=1)
    store = PostgresWorkerJobStore(
        async_session_factory,
        expected_job_type=JobType.FAST,
    )

    renewed = await store.renew_lease(
        JOB_UUID,
        worker_id,
        NOW + timedelta(minutes=10),
        expected_attempt_count,
    )

    saved = await load_job(async_session_factory)
    assert renewed is False
    assert saved.lease_owner == WORKER_ID
    assert saved.attempt_count == 3
    assert saved.lease_expires_at == previous_expiration


@pytest.mark.parametrize(
    "lease_expires_at",
    [
        NOW + timedelta(minutes=1),
        datetime(2000, 1, 1, tzinfo=UTC),
    ],
    ids=["future-lease", "expired-lease"],
)
async def test_is_processing_attempt_ignores_lease_expiration(
    async_session_factory: async_sessionmaker[AsyncSession],
    lease_expires_at: datetime,
) -> None:
    await persist_processing_job(
        async_session_factory,
        lease_expires_at=lease_expires_at,
    )
    store = PostgresWorkerJobStore(
        async_session_factory,
        expected_job_type=JobType.FAST,
    )

    is_processing = await store.is_processing_attempt(JOB_UUID, 3)

    assert is_processing is True


async def test_is_processing_attempt_rejects_another_attempt(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await persist_processing_job(async_session_factory, attempt_count=4)
    store = PostgresWorkerJobStore(
        async_session_factory,
        expected_job_type=JobType.FAST,
    )

    is_processing = await store.is_processing_attempt(JOB_UUID, 3)

    assert is_processing is False


@pytest.mark.parametrize("status", [JobStatus.COMPLETED, JobStatus.FAILED])
async def test_is_processing_attempt_rejects_terminal_jobs(
    async_session_factory: async_sessionmaker[AsyncSession],
    status: JobStatus,
) -> None:
    async with async_transaction(async_session_factory) as session:
        await JobRepository(session).add(
            TranscriptionJob(
                job_uuid=JOB_UUID,
                status=status,
                job_type=JobType.FAST,
                audio_uri=f"{JOB_UUID}/input.wav",
                dispatch_required=False,
                attempt_count=3,
            )
        )
    store = PostgresWorkerJobStore(
        async_session_factory,
        expected_job_type=JobType.FAST,
    )

    is_processing = await store.is_processing_attempt(JOB_UUID, 3)

    assert is_processing is False


async def test_is_processing_attempt_rejects_a_missing_job(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresWorkerJobStore(
        async_session_factory,
        expected_job_type=JobType.FAST,
    )

    is_processing = await store.is_processing_attempt(JOB_UUID, 3)

    assert is_processing is False
