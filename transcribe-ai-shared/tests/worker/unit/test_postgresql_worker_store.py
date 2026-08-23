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
    JobStatus,
    JobType,
    PostgresWorkerJobStore,
    TranscriptionJob,
    WorkerJobTypeMismatchError,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_JOB_UUID = UUID("87654321-4321-8765-4321-876543218765")
LEASE_EXPIRES_AT = datetime(2026, 8, 21, 12, 5, tzinfo=UTC)
EXPECTED_ATTEMPT_COUNT = 3


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
        self.calls: list[tuple[UUID, str, datetime, int]] = []
        self.get_calls: list[UUID] = []

    async def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        self.events.append("get_by_uuid")
        self.get_calls.append(job_uuid)
        return self.outcome

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> TranscriptionJob | None:
        self.events.append("claim")
        self.calls.append(
            (job_uuid, worker_id, lease_expires_at, expected_attempt_count)
        )
        return self.outcome


def make_job(
    *,
    audio_job_uuid: UUID = JOB_UUID,
    status: JobStatus = JobStatus.QUEUED,
    attempt_count: int = EXPECTED_ATTEMPT_COUNT,
    job_type: JobType = JobType.FAST,
) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=JOB_UUID,
        status=status,
        job_type=job_type,
        audio_uri=f"{audio_job_uuid}/input.wav",
        attempt_count=attempt_count,
    )


async def test_is_processing_attempt_reads_and_closes_a_short_session() -> None:
    events: list[str] = []
    session = object()

    @asynccontextmanager
    async def recording_session() -> AsyncGenerator[object, None]:
        events.append("session_enter")
        yield session
        events.append("session_close")

    repository = RecordingRepository(
        session,
        make_job(status=JobStatus.PROCESSING),
        events,
    )
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, recording_session),
        expected_job_type=JobType.FAST,
        repository_factory=lambda received_session: repository,
    )

    is_processing = await store.is_processing_attempt(
        JOB_UUID,
        EXPECTED_ATTEMPT_COUNT,
    )

    assert is_processing is True
    assert repository.session is session
    assert repository.get_calls == [JOB_UUID]
    assert events == ["session_enter", "get_by_uuid", "session_close"]


@pytest.mark.parametrize(
    "job",
    [
        None,
        make_job(status=JobStatus.QUEUED),
        make_job(status=JobStatus.COMPLETED),
        make_job(status=JobStatus.FAILED),
        make_job(
            status=JobStatus.PROCESSING,
            attempt_count=EXPECTED_ATTEMPT_COUNT + 1,
        ),
        make_job(
            status=JobStatus.PROCESSING,
            job_type=JobType.LONG_FORM_DIARIZATION,
        ),
    ],
    ids=[
        "missing",
        "queued",
        "completed",
        "failed",
        "attempt-mismatch",
        "job-type-mismatch",
    ],
)
async def test_is_processing_attempt_rejects_non_matching_job_state(
    job: TranscriptionJob | None,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def recording_session() -> AsyncGenerator[object, None]:
        yield object()

    repository = RecordingRepository(object(), job, events)
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, recording_session),
        expected_job_type=JobType.FAST,
        repository_factory=lambda _session: repository,
    )

    is_processing = await store.is_processing_attempt(
        JOB_UUID,
        EXPECTED_ATTEMPT_COUNT,
    )

    assert is_processing is False
    assert repository.get_calls == [JOB_UUID]


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

    claimed = await store.claim(
        JOB_UUID,
        "worker-fast-1",
        LEASE_EXPIRES_AT,
        EXPECTED_ATTEMPT_COUNT,
    )

    assert events == ["transaction_enter", "claim", "transaction_commit"]
    assert repository.session is session
    assert repository.calls == [
        (JOB_UUID, "worker-fast-1", LEASE_EXPIRES_AT, EXPECTED_ATTEMPT_COUNT),
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

    claimed = await store.claim(
        JOB_UUID,
        "worker-fast-1",
        LEASE_EXPIRES_AT,
        EXPECTED_ATTEMPT_COUNT,
    )

    assert claimed is None
    assert events == ["transaction_enter", "claim", "transaction_commit"]
    assert repository.calls == [
        (
            JOB_UUID,
            "worker-fast-1",
            LEASE_EXPIRES_AT,
            EXPECTED_ATTEMPT_COUNT,
        )
    ]


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
        await store.claim(
            JOB_UUID,
            "worker-fast-1",
            LEASE_EXPIRES_AT,
            EXPECTED_ATTEMPT_COUNT,
        )

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
    job.job_type = JobType.LONG_FORM_DIARIZATION
    repository = RecordingRepository(object(), job, events)
    store = PostgresWorkerJobStore(
        cast(AsyncSessionFactory, object()),
        expected_job_type=JobType.FAST,
        repository_factory=lambda _session: repository,
    )

    with pytest.raises(WorkerJobTypeMismatchError) as raised:
        await store.claim(
            JOB_UUID,
            "worker-fast-1",
            LEASE_EXPIRES_AT,
            EXPECTED_ATTEMPT_COUNT,
        )

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.expected_job_type is JobType.FAST
    assert raised.value.actual_job_type is JobType.LONG_FORM_DIARIZATION
    assert events == ["transaction_enter", "claim", "transaction_rollback"]
