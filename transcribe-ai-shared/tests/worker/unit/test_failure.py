from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError

import transcribe_ai_shared.worker.failure as failure_module
from transcribe_ai_shared import (
    AsyncSessionFactory,
    AudioLocation,
    ClaimedJob,
    ClassifiedTranscriptionFailure,
    JobStatus,
    JobType,
    TranscriptionFailureCategory,
    TranscriptionFailureResolution,
    TranscriptionFailureService,
    TranscriptionFailureTransitionError,
    TranscriptionFailureTransitionRejectedError,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
WORKER_ID = "worker-fast-1"


def make_job(*, attempt_count: int = 0) -> ClaimedJob:
    return ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=attempt_count,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )


def make_failure(
    category: TranscriptionFailureCategory,
) -> ClassifiedTranscriptionFailure:
    return ClassifiedTranscriptionFailure(
        category=category,
        error_code="SAFE_TRANSCRIPTION_ERROR",
    )


class RecordingJobRepository:
    def __init__(
        self,
        session: object,
        events: list[str],
        *,
        outcome: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.outcome = outcome
        self.error = error
        self.requeue_calls: list[tuple[UUID, str, int, str]] = []
        self.mark_failed_calls: list[tuple[UUID, str, int, str]] = []

    async def requeue_after_failure(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
        error_code: str,
    ) -> bool:
        self.events.append("requeue_after_failure")
        self.requeue_calls.append(
            (job_uuid, worker_id, expected_attempt_count, error_code)
        )
        if self.error is not None:
            raise self.error
        return self.outcome

    async def mark_failed(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
        error_code: str,
    ) -> bool:
        self.events.append("mark_failed")
        self.mark_failed_calls.append(
            (job_uuid, worker_id, expected_attempt_count, error_code)
        )
        if self.error is not None:
            raise self.error
        return self.outcome


def make_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_attempts: int = 3,
    repository_outcome: bool = True,
    repository_error: Exception | None = None,
    commit_error: Exception | None = None,
) -> tuple[TranscriptionFailureService, RecordingJobRepository, list[str]]:
    events: list[str] = []
    session = object()
    repository = RecordingJobRepository(
        session,
        events,
        outcome=repository_outcome,
        error=repository_error,
    )

    @asynccontextmanager
    async def recording_transaction(
        _session_factory: AsyncSessionFactory,
    ) -> AsyncGenerator[object, None]:
        events.append("transaction_begin")
        try:
            yield session
            if commit_error is not None:
                raise commit_error
        except BaseException:
            events.append("transaction_rollback")
            raise
        else:
            events.append("transaction_commit")

    def repository_factory(received_session: object) -> RecordingJobRepository:
        events.append("create_job_repository")
        assert received_session is session
        return repository

    monkeypatch.setattr(
        failure_module,
        "async_transaction",
        recording_transaction,
    )
    service = TranscriptionFailureService(
        cast(AsyncSessionFactory, object()),
        max_attempts=max_attempts,
        repository_factory=repository_factory,
    )
    return service, repository, events


async def test_retryable_failure_requeues_and_increments_attempt_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, events = make_service(monkeypatch, max_attempts=3)
    job = make_job(attempt_count=1)
    failure = make_failure(TranscriptionFailureCategory.RETRYABLE)

    resolution = await service.handle(
        job=job,
        worker_id=WORKER_ID,
        failure=failure,
    )

    assert resolution.status is JobStatus.QUEUED
    assert resolution.attempt_count == 2
    assert repository.requeue_calls == [
        (JOB_UUID, WORKER_ID, 1, "SAFE_TRANSCRIPTION_ERROR")
    ]
    assert repository.mark_failed_calls == []
    assert events == [
        "transaction_begin",
        "create_job_repository",
        "requeue_after_failure",
        "transaction_commit",
    ]


@pytest.mark.parametrize(
    ("category", "attempt_count", "max_attempts"),
    [
        (TranscriptionFailureCategory.PERMANENT, 0, 3),
        (TranscriptionFailureCategory.RETRYABLE, 2, 3),
        (TranscriptionFailureCategory.RETRYABLE, 0, 1),
    ],
)
async def test_failure_is_terminal_when_permanent_or_attempts_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    category: TranscriptionFailureCategory,
    attempt_count: int,
    max_attempts: int,
) -> None:
    service, repository, events = make_service(
        monkeypatch,
        max_attempts=max_attempts,
    )
    job = make_job(attempt_count=attempt_count)
    failure = make_failure(category)

    resolution = await service.handle(
        job=job,
        worker_id=WORKER_ID,
        failure=failure,
    )

    assert resolution.status is JobStatus.FAILED
    assert resolution.attempt_count == attempt_count
    assert repository.requeue_calls == []
    assert repository.mark_failed_calls == [
        (
            JOB_UUID,
            WORKER_ID,
            attempt_count,
            "SAFE_TRANSCRIPTION_ERROR",
        )
    ]
    assert events[-1] == "transaction_commit"


async def test_rejected_transition_rolls_back_and_exposes_stale_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, events = make_service(
        monkeypatch,
        repository_outcome=False,
    )
    job = make_job(attempt_count=1)

    with pytest.raises(TranscriptionFailureTransitionRejectedError) as raised:
        await service.handle(
            job=job,
            worker_id=WORKER_ID,
            failure=make_failure(TranscriptionFailureCategory.RETRYABLE),
        )

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == 1
    assert len(repository.requeue_calls) == 1
    assert events[-1] == "transaction_rollback"
    assert "transaction_commit" not in events


async def test_sqlalchemy_error_is_translated_after_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_error = SQLAlchemyError("database unavailable")
    service, _, events = make_service(
        monkeypatch,
        repository_error=database_error,
    )

    with pytest.raises(TranscriptionFailureTransitionError) as raised:
        await service.handle(
            job=make_job(),
            worker_id=WORKER_ID,
            failure=make_failure(TranscriptionFailureCategory.RETRYABLE),
        )

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.__cause__ is database_error
    assert events[-1] == "transaction_rollback"


async def test_commit_error_is_translated_without_reporting_a_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit_error = SQLAlchemyError("commit acknowledgement lost")
    service, _, events = make_service(
        monkeypatch,
        commit_error=commit_error,
    )

    with pytest.raises(TranscriptionFailureTransitionError) as raised:
        await service.handle(
            job=make_job(),
            worker_id=WORKER_ID,
            failure=make_failure(TranscriptionFailureCategory.RETRYABLE),
        )

    assert raised.value.__cause__ is commit_error
    assert events[-1] == "transaction_rollback"
    assert "transaction_commit" not in events


@pytest.mark.parametrize("max_attempts", [0, -1, True, 1.5, 2_147_483_649])
async def test_service_rejects_invalid_max_attempts(max_attempts) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        TranscriptionFailureService(
            cast(AsyncSessionFactory, object()),
            max_attempts=max_attempts,
        )


async def test_resolution_rejects_a_string_status_even_when_it_matches_the_enum() -> (
    None
):
    with pytest.raises(ValueError, match="status"):
        TranscriptionFailureResolution(  # type: ignore[arg-type]
            status="QUEUED",
            attempt_count=1,
        )
