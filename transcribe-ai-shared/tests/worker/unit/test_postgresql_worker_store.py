from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

import transcribe_ai_shared.worker.postgresql as postgresql_module
from transcribe_ai_shared import (
    AsyncSessionFactory,
    AudioLocation,
    ClaimedJob,
    InvalidAudioLocationError,
    JobType,
    PostgresWorkerJobStore,
    TranscriptionJob,
    WorkerJobTypeMismatchError,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_JOB_UUID = UUID("87654321-4321-8765-4321-876543218765")
LEASE_EXPIRES_AT = datetime(2026, 8, 21, 12, 5, tzinfo=UTC)


class RecordingRepository:
    def __init__(
        self,
        session: object,
        outcome: TranscriptionJob | None,
        events: list[str],
    ) -> None:
        self.session = session
        self.outcome = outcome
        self.events = events
        self.calls: list[tuple[UUID, str, datetime]] = []

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> TranscriptionJob | None:
        self.events.append("claim")
        self.calls.append((job_uuid, worker_id, lease_expires_at))
        return self.outcome


def make_job(*, audio_job_uuid: UUID = JOB_UUID) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        audio_uri=f"{audio_job_uuid}/input.wav",
        attempt_count=3,
    )


async def test_claim_commits_before_returning_a_detached_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    session = object()

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        events.append("transaction_enter")
        yield session
        events.append("transaction_commit")

    monkeypatch.setattr(
        postgresql_module,
        "async_transaction",
        recording_transaction,
    )
    repository = RecordingRepository(session, make_job(), events)
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, object()),
        expected_job_type=JobType.FAST,
        repository_factory=lambda received_session: repository,
    )

    claimed = await store.claim(JOB_UUID, "worker-fast-1", LEASE_EXPIRES_AT)

    assert events == ["transaction_enter", "claim", "transaction_commit"]
    assert repository.session is session
    assert repository.calls == [
        (JOB_UUID, "worker-fast-1", LEASE_EXPIRES_AT),
    ]
    assert claimed == ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=3,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )


async def test_rejected_claim_commits_the_short_no_op_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        events.append("transaction_enter")
        yield object()
        events.append("transaction_commit")

    monkeypatch.setattr(
        postgresql_module,
        "async_transaction",
        recording_transaction,
    )
    repository = RecordingRepository(object(), None, events)
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, object()),
        expected_job_type=JobType.FAST,
        repository_factory=lambda _session: repository,
    )

    claimed = await store.claim(JOB_UUID, "worker-fast-1", LEASE_EXPIRES_AT)

    assert claimed is None
    assert events == ["transaction_enter", "claim", "transaction_commit"]


async def test_invalid_audio_location_rolls_back_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        events.append("transaction_enter")
        try:
            yield object()
        except BaseException:
            events.append("transaction_rollback")
            raise
        else:
            events.append("transaction_commit")

    monkeypatch.setattr(
        postgresql_module,
        "async_transaction",
        recording_transaction,
    )
    repository = RecordingRepository(
        object(), make_job(audio_job_uuid=OTHER_JOB_UUID), events
    )
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, object()),
        expected_job_type=JobType.FAST,
        repository_factory=lambda _session: repository,
    )

    with pytest.raises(InvalidAudioLocationError, match="correspond"):
        await store.claim(JOB_UUID, "worker-fast-1", LEASE_EXPIRES_AT)

    assert events == ["transaction_enter", "claim", "transaction_rollback"]


async def test_mismatched_job_type_rolls_back_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        events.append("transaction_enter")
        try:
            yield object()
        except BaseException:
            events.append("transaction_rollback")
            raise
        else:
            events.append("transaction_commit")

    monkeypatch.setattr(
        postgresql_module,
        "async_transaction",
        recording_transaction,
    )
    job = make_job()
    job.job_type = JobType.BATCH
    repository = RecordingRepository(object(), job, events)
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, object()),
        expected_job_type=JobType.FAST,
        repository_factory=lambda _session: repository,
    )

    with pytest.raises(WorkerJobTypeMismatchError) as raised:
        await store.claim(JOB_UUID, "worker-fast-1", LEASE_EXPIRES_AT)

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.expected_job_type is JobType.FAST
    assert raised.value.actual_job_type is JobType.BATCH
    assert events == ["transaction_enter", "claim", "transaction_rollback"]
