import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from transcribe_ai_shared import (
    JobRepository,
    JobStatus,
    JobType,
    ResultRepository,
    TranscriptionJob,
    TranscriptionResult,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


@contextmanager
def capture_transcription_job_selects(
    engine: Engine,
) -> Generator[list[str], None, None]:
    """Capture uniquement les SELECT visant la table des jobs."""
    statements: list[str] = []

    def record_statement(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ) -> None:
        normalized_statement = " ".join(statement.lower().split())
        if normalized_statement.startswith("select ") and (
            " from transcription_jobs" in normalized_statement
        ):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)


def make_job(**overrides) -> TranscriptionJob:
    values = {
        "job_type": JobType.FAST,
        "audio_uri": "audio/job.wav",
    }
    values.update(overrides)
    return TranscriptionJob(**values)


async def persist_job(
    factory: async_sessionmaker[AsyncSession],
    **overrides,
) -> UUID:
    async with async_transaction(factory) as session:
        job = make_job(**overrides)
        await JobRepository(session).add(job)
        return job.job_uuid


async def read_job(
    factory: async_sessionmaker[AsyncSession],
    job_uuid: UUID,
) -> TranscriptionJob | None:
    async with factory() as session:
        return await JobRepository(session).get_by_uuid(job_uuid)


async def persist_result(
    factory: async_sessionmaker[AsyncSession],
    job_uuid: UUID,
) -> None:
    async with async_transaction(factory) as session:
        await ResultRepository(session).add(
            TranscriptionResult(
                job_uuid=job_uuid,
                result={"text": "Transcription terminée."},
            )
        )


async def test_add_persists_a_valid_job(async_session_factory) -> None:
    job = make_job()

    async with async_transaction(async_session_factory) as session:
        await JobRepository(session).add(job)
        job_uuid = job.job_uuid

    saved = await read_job(async_session_factory, job_uuid)
    assert saved is not None
    assert saved.job_uuid == job_uuid
    assert saved.job_type is JobType.FAST
    assert saved.status is JobStatus.QUEUED
    assert saved.audio_uri == "audio/job.wav"
    assert saved.dispatch_required is True
    assert saved.last_dispatched_at is None


async def test_add_does_not_commit_the_callers_transaction(
    async_session_factory,
) -> None:
    job_uuid = uuid4()

    with pytest.raises(RuntimeError, match="rollback requested"):
        async with async_transaction(async_session_factory) as session:
            await JobRepository(session).add(make_job(job_uuid=job_uuid))
            raise RuntimeError("rollback requested")

    assert await read_job(async_session_factory, job_uuid) is None


async def test_get_by_uuid_returns_an_existing_job(async_session_factory) -> None:
    job_uuid = await persist_job(async_session_factory)

    saved = await read_job(async_session_factory, job_uuid)

    assert saved is not None
    assert saved.job_uuid == job_uuid


async def test_get_by_uuid_returns_none_for_an_unknown_job(
    async_session_factory,
) -> None:
    assert await read_job(async_session_factory, uuid4()) is None


async def test_get_jobs_by_uuids_returns_known_jobs_and_ignores_unknown(
    async_session_factory,
) -> None:
    first_job_uuid = await persist_job(async_session_factory)
    second_job_uuid = await persist_job(async_session_factory)

    async with async_session_factory() as session:
        jobs = await JobRepository(session).get_jobs_by_uuids(
            [first_job_uuid, uuid4(), second_job_uuid]
        )

    assert {job.job_uuid for job in jobs} == {
        first_job_uuid,
        second_job_uuid,
    }


async def test_get_jobs_by_uuids_returns_each_job_once_for_duplicate_uuids(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(async_session_factory)

    async with async_session_factory() as session:
        jobs = await JobRepository(session).get_jobs_by_uuids(
            [job_uuid, job_uuid, job_uuid]
        )

    assert [job.job_uuid for job in jobs] == [job_uuid]


async def test_get_jobs_by_uuids_empty_collection_does_not_query_database(
    async_session_factory,
) -> None:
    async with async_session_factory() as session:
        engine = session.sync_session.get_bind()
        assert isinstance(engine, Engine)
        with capture_transcription_job_selects(engine) as statements:
            jobs = await JobRepository(session).get_jobs_by_uuids([])

    assert jobs == []
    assert statements == []


async def test_get_jobs_by_uuids_uses_one_select_below_batch_size(
    async_session_factory,
) -> None:
    job_uuids = [
        await persist_job(async_session_factory),
        await persist_job(async_session_factory),
    ]

    async with async_session_factory() as session:
        engine = session.sync_session.get_bind()
        assert isinstance(engine, Engine)
        with capture_transcription_job_selects(engine) as statements:
            jobs = await JobRepository(session).get_jobs_by_uuids(job_uuids)

    assert {job.job_uuid for job in jobs} == set(job_uuids)
    assert len(statements) == 1


async def test_get_jobs_by_uuids_splits_large_inputs_into_batches(
    async_session_factory,
) -> None:
    unknown_job_uuids = [uuid4() for _ in range(1_001)]

    async with async_session_factory() as session:
        engine = session.sync_session.get_bind()
        assert isinstance(engine, Engine)
        with capture_transcription_job_selects(engine) as statements:
            jobs = await JobRepository(session).get_jobs_by_uuids(unknown_job_uuids)

    assert jobs == []
    assert len(statements) == 2


async def test_find_jobs_requiring_dispatch_filters_orders_and_limits(
    async_session_factory,
) -> None:
    oldest_uuid = await persist_job(
        async_session_factory,
        job_uuid=UUID("00000000-0000-0000-0000-000000000003"),
        created_at=NOW - timedelta(minutes=2),
        dispatch_required=True,
    )
    first_tied_uuid = await persist_job(
        async_session_factory,
        job_uuid=UUID("00000000-0000-0000-0000-000000000001"),
        created_at=NOW - timedelta(minutes=1),
        dispatch_required=True,
    )
    second_tied_uuid = await persist_job(
        async_session_factory,
        job_uuid=UUID("00000000-0000-0000-0000-000000000002"),
        created_at=NOW - timedelta(minutes=1),
        dispatch_required=True,
    )
    await persist_job(
        async_session_factory,
        created_at=NOW - timedelta(minutes=10),
        dispatch_required=False,
    )
    await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        created_at=NOW - timedelta(minutes=10),
        dispatch_required=True,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
    )

    async with async_session_factory() as session:
        repository = JobRepository(session)
        limited_jobs = await repository.find_jobs_requiring_dispatch(limit=2)
        all_jobs = await repository.find_jobs_requiring_dispatch(limit=10)

    assert [job.job_uuid for job in limited_jobs] == [
        oldest_uuid,
        first_tied_uuid,
    ]
    assert [job.job_uuid for job in all_jobs] == [
        oldest_uuid,
        first_tied_uuid,
        second_tied_uuid,
    ]


async def test_mark_dispatched_updates_a_job_requiring_dispatch(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        dispatch_required=True,
        attempt_count=2,
    )

    async with async_session_factory.begin() as session:
        dispatched = await JobRepository(session).mark_dispatched(job_uuid, 2, NOW)

    saved = await read_job(async_session_factory, job_uuid)
    assert dispatched is True
    assert saved is not None
    assert saved.dispatch_required is False
    assert saved.last_dispatched_at == NOW
    assert saved.attempt_count == 2


async def test_mark_dispatched_returns_false_for_an_unknown_job(
    async_session_factory,
) -> None:
    async with async_session_factory.begin() as session:
        dispatched = await JobRepository(session).mark_dispatched(uuid4(), 0, NOW)

    assert dispatched is False


async def test_mark_dispatched_refuses_a_job_already_marked_as_dispatched(
    async_session_factory,
) -> None:
    previous_dispatch = NOW - timedelta(minutes=5)
    job_uuid = await persist_job(
        async_session_factory,
        dispatch_required=False,
        last_dispatched_at=previous_dispatch,
    )

    async with async_session_factory.begin() as session:
        dispatched = await JobRepository(session).mark_dispatched(job_uuid, 0, NOW)

    saved = await read_job(async_session_factory, job_uuid)
    assert dispatched is False
    assert saved is not None
    assert saved.dispatch_required is False
    assert saved.last_dispatched_at == previous_dispatch


async def test_mark_dispatched_accepts_a_job_already_claimed_by_a_worker(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        dispatch_required=True,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        attempt_count=3,
    )

    async with async_session_factory.begin() as session:
        dispatched = await JobRepository(session).mark_dispatched(job_uuid, 3, NOW)

    saved = await read_job(async_session_factory, job_uuid)
    assert dispatched is True
    assert saved is not None
    assert saved.status is JobStatus.PROCESSING
    assert saved.dispatch_required is False
    assert saved.last_dispatched_at == NOW


async def test_mark_dispatched_refuses_a_stale_attempt_after_requeue(
    async_session_factory,
) -> None:
    previous_dispatch = NOW - timedelta(minutes=20)
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        dispatch_required=True,
        last_dispatched_at=previous_dispatch,
        lease_owner="worker-fast-1",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        attempt_count=2,
    )

    async with async_session_factory.begin() as session:
        repository = JobRepository(session)
        requeued = await repository.requeue_expired_job(job_uuid)
        dispatched = await repository.mark_dispatched(job_uuid, 2, NOW)

    saved = await read_job(async_session_factory, job_uuid)
    assert requeued is not None
    assert dispatched is False
    assert saved is not None
    assert saved.status is JobStatus.QUEUED
    assert saved.attempt_count == 3
    assert saved.dispatch_required is True
    assert saved.last_dispatched_at == previous_dispatch


async def test_mark_dispatched_does_not_commit_the_callers_transaction(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        dispatch_required=True,
        attempt_count=4,
    )

    with pytest.raises(RuntimeError, match="rollback requested"):
        async with async_transaction(async_session_factory) as session:
            dispatched = await JobRepository(session).mark_dispatched(
                job_uuid,
                4,
                NOW,
            )
            assert dispatched is True
            raise RuntimeError("rollback requested")

    saved = await read_job(async_session_factory, job_uuid)
    assert saved is not None
    assert saved.dispatch_required is True
    assert saved.last_dispatched_at is None
    assert saved.attempt_count == 4


async def test_claim_moves_a_queued_job_to_processing(
    async_session_factory,
) -> None:
    last_dispatched_at = NOW - timedelta(minutes=1)
    job_uuid = await persist_job(
        async_session_factory,
        dispatch_required=False,
        last_dispatched_at=last_dispatched_at,
    )
    lease_expires_at = NOW + timedelta(minutes=5)

    async with async_session_factory.begin() as session:
        claimed = await JobRepository(session).claim(
            job_uuid,
            "worker-fast-1",
            lease_expires_at,
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert claimed is not None
    assert saved is not None
    assert saved.status is JobStatus.PROCESSING
    assert saved.dispatch_required is False
    assert saved.last_dispatched_at == last_dispatched_at
    assert saved.lease_owner == "worker-fast-1"
    assert saved.lease_expires_at == lease_expires_at
    assert saved.started_at is not None


@pytest.mark.parametrize(
    "status",
    [JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.FAILED],
)
async def test_claim_refuses_a_job_that_is_not_queued(
    async_session_factory,
    status: JobStatus,
) -> None:
    job_uuid = await persist_job(async_session_factory, status=status)

    async with async_session_factory.begin() as session:
        claimed = await JobRepository(session).claim(
            job_uuid,
            "worker-fast-1",
            NOW + timedelta(minutes=5),
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert claimed is None
    assert saved is not None
    assert saved.status is status
    assert saved.lease_owner is None


async def test_claim_is_atomic_between_two_workers(async_session_factory) -> None:
    job_uuid = await persist_job(async_session_factory)
    barrier = asyncio.Barrier(2)

    async def attempt_claim(
        worker_id: str,
        lease_expires_at: datetime,
    ) -> tuple[str, datetime, TranscriptionJob | None]:
        async with async_transaction(async_session_factory) as session:
            await barrier.wait()
            claimed = await JobRepository(session).claim(
                job_uuid,
                worker_id,
                lease_expires_at,
            )
        return worker_id, lease_expires_at, claimed

    attempts = await asyncio.wait_for(
        asyncio.gather(
            attempt_claim("worker-fast-1", NOW + timedelta(minutes=5)),
            attempt_claim("worker-fast-2", NOW + timedelta(minutes=10)),
        ),
        timeout=10,
    )

    winners = [attempt for attempt in attempts if attempt[2] is not None]
    assert len(winners) == 1
    winning_worker_id, winning_expiration, _ = winners[0]
    saved = await read_job(async_session_factory, job_uuid)
    assert saved is not None
    assert saved.lease_owner == winning_worker_id
    assert saved.lease_expires_at == winning_expiration


async def test_claim_does_not_commit_the_callers_transaction(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(async_session_factory)

    with pytest.raises(RuntimeError, match="rollback requested"):
        async with async_transaction(async_session_factory) as session:
            claimed = await JobRepository(session).claim(
                job_uuid,
                "worker-fast-1",
                NOW + timedelta(minutes=5),
            )
            assert claimed is not None
            raise RuntimeError("rollback requested")

    saved = await read_job(async_session_factory, job_uuid)
    assert saved is not None
    assert saved.status is JobStatus.QUEUED
    assert saved.lease_owner is None
    assert saved.lease_expires_at is None
    assert saved.started_at is None


async def test_claim_preserves_the_first_start_time(async_session_factory) -> None:
    first_started_at = NOW - timedelta(hours=1)
    job_uuid = await persist_job(
        async_session_factory,
        started_at=first_started_at,
    )

    async with async_session_factory.begin() as session:
        claimed = await JobRepository(session).claim(
            job_uuid,
            "worker-fast-1",
            NOW + timedelta(minutes=5),
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert claimed is not None
    assert saved is not None
    assert saved.started_at == first_started_at


async def test_renew_lease_updates_the_owners_expiration(
    async_session_factory,
) -> None:
    previous_expiration = NOW + timedelta(minutes=1)
    renewed_expiration = NOW + timedelta(minutes=10)
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-fast-1",
        lease_expires_at=previous_expiration,
    )

    async with async_session_factory.begin() as session:
        renewed = await JobRepository(session).renew_lease(
            job_uuid,
            "worker-fast-1",
            renewed_expiration,
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert renewed is True
    assert saved is not None
    assert saved.lease_expires_at == renewed_expiration


@pytest.mark.parametrize(
    ("status", "actual_owner", "requesting_owner"),
    [
        (JobStatus.QUEUED, "worker-fast-1", "worker-fast-1"),
        (JobStatus.COMPLETED, "worker-fast-1", "worker-fast-1"),
        (JobStatus.FAILED, "worker-fast-1", "worker-fast-1"),
        (JobStatus.PROCESSING, "worker-fast-1", "worker-fast-2"),
    ],
)
async def test_renew_lease_refuses_an_invalid_status_or_owner(
    async_session_factory,
    status: JobStatus,
    actual_owner: str,
    requesting_owner: str,
) -> None:
    previous_expiration = NOW + timedelta(minutes=1)
    job_uuid = await persist_job(
        async_session_factory,
        status=status,
        lease_owner=actual_owner,
        lease_expires_at=previous_expiration,
    )

    async with async_session_factory.begin() as session:
        renewed = await JobRepository(session).renew_lease(
            job_uuid,
            requesting_owner,
            NOW + timedelta(minutes=10),
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert renewed is False
    assert saved is not None
    assert saved.lease_owner == actual_owner
    assert saved.lease_expires_at == previous_expiration


async def test_find_expired_processing_jobs_filters_orders_and_limits(
    async_session_factory,
) -> None:
    oldest_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-1",
        lease_expires_at=NOW - timedelta(minutes=10),
    )
    newest_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-2",
        lease_expires_at=NOW - timedelta(minutes=1),
    )
    await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-3",
        lease_expires_at=NOW,
    )
    await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-4",
        lease_expires_at=NOW + timedelta(minutes=1),
    )
    await persist_job(
        async_session_factory,
        status=JobStatus.FAILED,
        lease_owner="worker-5",
        lease_expires_at=NOW - timedelta(minutes=20),
    )

    async with async_session_factory() as session:
        repository = JobRepository(session)
        limited_jobs = await repository.find_expired_processing_jobs(NOW, limit=1)
        all_jobs = await repository.find_expired_processing_jobs(NOW, limit=10)

    assert [job.job_uuid for job in limited_jobs] == [oldest_uuid]
    assert [job.job_uuid for job in all_jobs] == [oldest_uuid, newest_uuid]


async def test_requeue_expired_job_resets_lease_and_increments_attempt_count(
    async_session_factory,
) -> None:
    last_dispatched_at = NOW - timedelta(minutes=20)
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        dispatch_required=False,
        last_dispatched_at=last_dispatched_at,
        lease_owner="worker-fast-1",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        attempt_count=2,
    )

    async with async_session_factory.begin() as session:
        requeued = await JobRepository(session).requeue_expired_job(job_uuid)

    saved = await read_job(async_session_factory, job_uuid)
    assert requeued is not None
    assert saved is not None
    assert saved.status is JobStatus.QUEUED
    assert saved.dispatch_required is True
    assert saved.last_dispatched_at == last_dispatched_at
    assert saved.lease_owner is None
    assert saved.lease_expires_at is None
    assert saved.attempt_count == 3


@pytest.mark.parametrize(
    ("status", "lease_offset"),
    [
        (JobStatus.PROCESSING, timedelta(hours=1)),
        (JobStatus.QUEUED, -timedelta(hours=1)),
        (JobStatus.COMPLETED, -timedelta(hours=1)),
        (JobStatus.FAILED, -timedelta(hours=1)),
    ],
)
async def test_requeue_expired_job_refuses_an_available_or_non_processing_job(
    async_session_factory,
    status: JobStatus,
    lease_offset: timedelta,
) -> None:
    lease_expires_at = datetime.now(timezone.utc) + lease_offset
    last_dispatched_at = NOW - timedelta(minutes=20)
    job_uuid = await persist_job(
        async_session_factory,
        status=status,
        dispatch_required=False,
        last_dispatched_at=last_dispatched_at,
        lease_owner="worker-fast-1",
        lease_expires_at=lease_expires_at,
        attempt_count=2,
    )

    async with async_session_factory.begin() as session:
        requeued = await JobRepository(session).requeue_expired_job(job_uuid)

    saved = await read_job(async_session_factory, job_uuid)
    assert requeued is None
    assert saved is not None
    assert saved.status is status
    assert saved.dispatch_required is False
    assert saved.last_dispatched_at == last_dispatched_at
    assert saved.lease_owner == "worker-fast-1"
    assert saved.lease_expires_at == lease_expires_at
    assert saved.attempt_count == 2


async def test_mutations_refuse_an_unknown_job(async_session_factory) -> None:
    unknown_uuid = uuid4()

    async with async_session_factory.begin() as session:
        repository = JobRepository(session)
        claimed = await repository.claim(
            unknown_uuid,
            "worker-fast-1",
            NOW + timedelta(minutes=5),
        )
        renewed = await repository.renew_lease(
            unknown_uuid,
            "worker-fast-1",
            NOW + timedelta(minutes=10),
        )
        requeued = await repository.requeue_expired_job(unknown_uuid)
        failed = await repository.mark_failed(
            unknown_uuid,
            "worker-fast-1",
            "MODEL_ERROR",
        )
        completed = await repository.mark_completed(
            unknown_uuid,
            "worker-fast-1",
            0,
        )

    assert claimed is None
    assert renewed is False
    assert requeued is None
    assert failed is False
    assert completed is False


async def test_requeue_expired_job_only_increments_once(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-fast-1",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )

    async with async_session_factory.begin() as session:
        repository = JobRepository(session)
        first_requeue = await repository.requeue_expired_job(job_uuid)
        second_requeue = await repository.requeue_expired_job(job_uuid)

    saved = await read_job(async_session_factory, job_uuid)
    assert first_requeue is not None
    assert second_requeue is None
    assert saved is not None
    assert saved.attempt_count == 1


async def test_mark_failed_updates_a_job_owned_by_the_worker(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
    )

    async with async_session_factory.begin() as session:
        failed = await JobRepository(session).mark_failed(
            job_uuid,
            "worker-fast-1",
            "MODEL_ERROR",
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert failed is True
    assert saved is not None
    assert saved.status is JobStatus.FAILED
    assert saved.last_error == "MODEL_ERROR"
    assert saved.completed_at is not None
    assert saved.completed_at.tzinfo is not None


@pytest.mark.parametrize(
    ("status", "requesting_owner"),
    [
        (JobStatus.QUEUED, "worker-fast-1"),
        (JobStatus.COMPLETED, "worker-fast-1"),
        (JobStatus.FAILED, "worker-fast-1"),
        (JobStatus.PROCESSING, "worker-fast-2"),
    ],
)
async def test_mark_failed_refuses_an_invalid_status_or_owner(
    async_session_factory,
    status: JobStatus,
    requesting_owner: str,
) -> None:
    previous_completed_at = (
        NOW - timedelta(hours=1)
        if status in {JobStatus.COMPLETED, JobStatus.FAILED}
        else None
    )
    job_uuid = await persist_job(
        async_session_factory,
        status=status,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        completed_at=previous_completed_at,
    )

    async with async_session_factory.begin() as session:
        failed = await JobRepository(session).mark_failed(
            job_uuid,
            requesting_owner,
            "MODEL_ERROR",
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert failed is False
    assert saved is not None
    assert saved.status is status
    assert saved.last_error is None
    assert saved.lease_owner == "worker-fast-1"
    assert saved.completed_at == previous_completed_at


async def test_mark_completed_updates_a_job_owned_by_the_worker(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        attempt_count=3,
    )

    async with async_session_factory.begin() as session:
        await ResultRepository(session).add(
            TranscriptionResult(
                job_uuid=job_uuid,
                result={"text": "Transcription terminée."},
            )
        )
        completed = await JobRepository(session).mark_completed(
            job_uuid,
            "worker-fast-1",
            3,
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert completed is True
    assert saved is not None
    assert saved.status is JobStatus.COMPLETED
    assert saved.completed_at is not None
    assert saved.completed_at.tzinfo is not None


async def test_mark_completed_refuses_a_job_without_a_result(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        attempt_count=2,
    )

    async with async_session_factory.begin() as session:
        completed = await JobRepository(session).mark_completed(
            job_uuid,
            "worker-fast-1",
            2,
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert completed is False
    assert saved is not None
    assert saved.status is JobStatus.PROCESSING
    assert saved.completed_at is None


async def test_mark_completed_refuses_a_stale_attempt(
    async_session_factory,
) -> None:
    job_uuid = await persist_job(
        async_session_factory,
        status=JobStatus.PROCESSING,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        attempt_count=2,
    )
    await persist_result(async_session_factory, job_uuid)

    async with async_session_factory.begin() as session:
        completed = await JobRepository(session).mark_completed(
            job_uuid,
            "worker-fast-1",
            1,
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert completed is False
    assert saved is not None
    assert saved.status is JobStatus.PROCESSING
    assert saved.attempt_count == 2
    assert saved.completed_at is None


@pytest.mark.parametrize(
    ("status", "requesting_owner"),
    [
        (JobStatus.QUEUED, "worker-fast-1"),
        (JobStatus.COMPLETED, "worker-fast-1"),
        (JobStatus.FAILED, "worker-fast-1"),
        (JobStatus.PROCESSING, "worker-fast-2"),
    ],
)
async def test_mark_completed_refuses_an_invalid_status_or_owner(
    async_session_factory,
    status: JobStatus,
    requesting_owner: str,
) -> None:
    previous_completed_at = (
        NOW - timedelta(hours=1)
        if status in {JobStatus.COMPLETED, JobStatus.FAILED}
        else None
    )
    job_uuid = await persist_job(
        async_session_factory,
        status=status,
        lease_owner="worker-fast-1",
        lease_expires_at=NOW + timedelta(minutes=5),
        completed_at=previous_completed_at,
    )
    await persist_result(async_session_factory, job_uuid)

    async with async_session_factory.begin() as session:
        completed = await JobRepository(session).mark_completed(
            job_uuid,
            requesting_owner,
            0,
        )

    saved = await read_job(async_session_factory, job_uuid)
    assert completed is False
    assert saved is not None
    assert saved.status is status
    assert saved.lease_owner == "worker-fast-1"
    assert saved.completed_at == previous_completed_at
