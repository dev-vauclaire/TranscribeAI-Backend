from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest

import dispatcher.postgresql as postgresql_module
from dispatcher.models import (
    DispatchJobSnapshot,
    ExpiredJobSnapshot,
    StaleDispatchSnapshot,
)
from dispatcher.postgresql import PostgresDispatchJobStore
from transcribe_ai_shared import (
    AsyncSessionFactory,
    JobType,
    TranscriptionJob,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("24335591-c566-4eb6-b4d0-7bf34db54b6c")
LAST_DISPATCHED_AT = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)
LEASE_EXPIRES_AT = datetime(2026, 8, 21, 10, 55, tzinfo=UTC)


class RecordingReadContext:
    def __init__(self) -> None:
        self.session = object()
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> object:
        self.entered = True
        return self.session

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True


class RecordingSessionFactory:
    def __init__(self, read_context: RecordingReadContext) -> None:
        self.read_context = read_context

    def __call__(self) -> RecordingReadContext:
        return self.read_context


class RecordingRepository:
    def __init__(
        self,
        session: object,
        jobs: list[TranscriptionJob],
    ) -> None:
        self.session = session
        self.jobs = jobs
        self.find_limits: list[int] = []
        self.mark_calls: list[tuple[UUID, int, datetime | None]] = []
        self.find_stale_calls: list[tuple[int, int]] = []
        self.rearm_calls: list[tuple[UUID, int, datetime, int]] = []
        self.find_expired_calls: list[int] = []
        self.recover_calls: list[tuple[UUID, int, datetime, bool]] = []

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[TranscriptionJob]:
        self.find_limits.append(limit)
        return self.jobs

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        expected_last_dispatched_at: datetime | None,
    ) -> bool:
        self.mark_calls.append(
            (job_uuid, expected_attempt_count, expected_last_dispatched_at),
        )
        return True

    async def find_stale_dispatched_jobs(
        self,
        *,
        limit: int,
        reconciliation_timeout_seconds: int,
    ) -> list[TranscriptionJob]:
        self.find_stale_calls.append((limit, reconciliation_timeout_seconds))
        return self.jobs

    async def rearm_stale_dispatch(
        self,
        *,
        job_uuid: UUID,
        expected_attempt_count: int,
        expected_last_dispatched_at: datetime,
        reconciliation_timeout_seconds: int,
    ) -> bool:
        self.rearm_calls.append(
            (
                job_uuid,
                expected_attempt_count,
                expected_last_dispatched_at,
                reconciliation_timeout_seconds,
            )
        )
        return True

    async def find_expired_processing_jobs(
        self,
        limit: int,
    ) -> list[TranscriptionJob]:
        self.find_expired_calls.append(limit)
        return self.jobs

    async def recover_expired_job(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        expected_lease_expires_at: datetime,
        *,
        should_retry: bool,
    ) -> bool:
        self.recover_calls.append(
            (
                job_uuid,
                expected_attempt_count,
                expected_lease_expires_at,
                should_retry,
            )
        )
        return True


def make_job(
    *,
    last_dispatched_at: datetime | None = LAST_DISPATCHED_AT,
    lease_expires_at: datetime | None = LEASE_EXPIRES_AT,
) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=JOB_UUID,
        job_type=JobType.LONG_FORM_DIARIZATION,
        audio_uri=f"{JOB_UUID}/input.wav",
        attempt_count=3,
        last_dispatched_at=last_dispatched_at,
        lease_owner=(
            "worker-long-form-diarization-1" if lease_expires_at is not None else None
        ),
        lease_expires_at=lease_expires_at,
    )


def make_store(
    read_context: RecordingReadContext,
    repository: RecordingRepository,
) -> PostgresDispatchJobStore:
    return PostgresDispatchJobStore(
        cast(AsyncSessionFactory, RecordingSessionFactory(read_context)),
        repository_factory=lambda session: repository,
    )


async def test_find_snapshots_jobs_and_closes_the_read_session() -> None:
    read_context = RecordingReadContext()
    repository = RecordingRepository(read_context.session, [make_job()])
    store = make_store(read_context, repository)

    snapshots = await store.find_jobs_requiring_dispatch(limit=25)

    assert snapshots == [
        DispatchJobSnapshot(
            job_uuid=JOB_UUID,
            job_type=JobType.LONG_FORM_DIARIZATION,
            attempt_count=3,
            last_dispatched_at=LAST_DISPATCHED_AT,
        ),
    ]
    assert repository.find_limits == [25]
    assert read_context.entered is True
    assert read_context.exited is True


async def test_each_confirmation_uses_a_distinct_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_sessions: list[object] = []
    repositories: list[RecordingRepository] = []

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        session = object()
        transaction_sessions.append(session)
        yield session

    def repository_factory(session: object) -> RecordingRepository:
        repository = RecordingRepository(session, [])
        repositories.append(repository)
        return repository

    monkeypatch.setattr(postgresql_module, "async_transaction", recording_transaction)
    store = PostgresDispatchJobStore(
        cast(
            AsyncSessionFactory,
            RecordingSessionFactory(RecordingReadContext()),
        ),
        repository_factory=repository_factory,
    )
    first_snapshot = DispatchJobSnapshot(
        JOB_UUID,
        JobType.FAST,
        2,
        None,
    )
    second_snapshot = DispatchJobSnapshot(
        JOB_UUID,
        JobType.FAST,
        3,
        LAST_DISPATCHED_AT,
    )

    first_result = await store.mark_dispatched(first_snapshot)
    second_result = await store.mark_dispatched(second_snapshot)

    assert first_result is True
    assert second_result is True
    assert len(transaction_sessions) == 2
    assert transaction_sessions[0] is not transaction_sessions[1]
    assert [repository.session for repository in repositories] == transaction_sessions
    assert repositories[0].mark_calls == [(JOB_UUID, 2, None)]
    assert repositories[1].mark_calls == [(JOB_UUID, 3, LAST_DISPATCHED_AT)]


async def test_find_stale_dispatches_detaches_the_reconciliation_guards() -> None:
    read_context = RecordingReadContext()
    repository = RecordingRepository(read_context.session, [make_job()])
    store = make_store(read_context, repository)

    snapshots = await store.find_stale_dispatched_jobs(
        limit=15,
        reconciliation_timeout_seconds=90,
    )

    assert snapshots == [
        StaleDispatchSnapshot(
            job_uuid=JOB_UUID,
            attempt_count=3,
            last_dispatched_at=LAST_DISPATCHED_AT,
        )
    ]
    assert repository.find_stale_calls == [(15, 90)]
    assert read_context.entered is True
    assert read_context.exited is True


async def test_stale_dispatch_rearm_uses_a_distinct_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_sessions: list[object] = []
    repositories: list[RecordingRepository] = []

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        session = object()
        transaction_sessions.append(session)
        yield session

    def repository_factory(session: object) -> RecordingRepository:
        repository = RecordingRepository(session, [])
        repositories.append(repository)
        return repository

    monkeypatch.setattr(postgresql_module, "async_transaction", recording_transaction)
    store = PostgresDispatchJobStore(
        cast(
            AsyncSessionFactory,
            RecordingSessionFactory(RecordingReadContext()),
        ),
        repository_factory=repository_factory,
    )
    snapshot = StaleDispatchSnapshot(JOB_UUID, 3, LAST_DISPATCHED_AT)

    first_result = await store.rearm_stale_dispatch(snapshot, 90)
    second_result = await store.rearm_stale_dispatch(snapshot, 120)

    assert first_result is True
    assert second_result is True
    assert len(transaction_sessions) == 2
    assert transaction_sessions[0] is not transaction_sessions[1]
    assert repositories[0].rearm_calls == [(JOB_UUID, 3, LAST_DISPATCHED_AT, 90)]
    assert repositories[1].rearm_calls == [(JOB_UUID, 3, LAST_DISPATCHED_AT, 120)]


async def test_find_stale_dispatches_rejects_an_inconsistent_repository_row() -> None:
    read_context = RecordingReadContext()
    repository = RecordingRepository(
        read_context.session,
        [make_job(last_dispatched_at=None)],
    )
    store = make_store(read_context, repository)

    with pytest.raises(ValueError, match="date de publication"):
        await store.find_stale_dispatched_jobs(1, 90)

    assert read_context.exited is True


async def test_find_expired_jobs_detaches_the_recovery_guards() -> None:
    read_context = RecordingReadContext()
    repository = RecordingRepository(read_context.session, [make_job()])
    store = make_store(read_context, repository)

    snapshots = await store.find_expired_jobs(limit=15)

    assert snapshots == [
        ExpiredJobSnapshot(
            job_uuid=JOB_UUID,
            attempt_count=3,
            lease_expires_at=LEASE_EXPIRES_AT,
        )
    ]
    assert repository.find_expired_calls == [15]
    assert read_context.entered is True
    assert read_context.exited is True


async def test_expired_job_recovery_uses_a_distinct_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction_sessions: list[object] = []
    repositories: list[RecordingRepository] = []

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        session = object()
        transaction_sessions.append(session)
        yield session

    def repository_factory(session: object) -> RecordingRepository:
        repository = RecordingRepository(session, [])
        repositories.append(repository)
        return repository

    monkeypatch.setattr(postgresql_module, "async_transaction", recording_transaction)
    store = PostgresDispatchJobStore(
        cast(
            AsyncSessionFactory,
            RecordingSessionFactory(RecordingReadContext()),
        ),
        repository_factory=repository_factory,
    )
    snapshot = ExpiredJobSnapshot(
        job_uuid=JOB_UUID,
        attempt_count=2,
        lease_expires_at=LEASE_EXPIRES_AT,
    )

    first_result = await store.recover_expired_job(snapshot, should_retry=True)
    second_result = await store.recover_expired_job(snapshot, should_retry=False)

    assert first_result is True
    assert second_result is True
    assert len(transaction_sessions) == 2
    assert repositories[0].session is transaction_sessions[0]
    assert repositories[1].session is transaction_sessions[1]
    assert repositories[0].recover_calls == [(JOB_UUID, 2, LEASE_EXPIRES_AT, True)]
    assert repositories[1].recover_calls == [(JOB_UUID, 2, LEASE_EXPIRES_AT, False)]


async def test_find_expired_jobs_rejects_an_inconsistent_repository_row() -> None:
    read_context = RecordingReadContext()
    repository = RecordingRepository(
        read_context.session,
        [make_job(lease_expires_at=None)],
    )
    store = make_store(read_context, repository)

    with pytest.raises(ValueError, match="échéance de lease"):
        await store.find_expired_jobs(limit=1)

    assert read_context.exited is True
