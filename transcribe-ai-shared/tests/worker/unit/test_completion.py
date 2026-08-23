from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError

import transcribe_ai_shared.worker.completion as completion_module
from transcribe_ai_shared import (
    AsyncSessionFactory,
    AudioLocation,
    ClaimedJob,
    JobType,
    TranscriptionCompletionError,
    TranscriptionCompletionRejectedError,
    TranscriptionCompletionService,
    TranscriptionOutput,
    TranscriptionResult,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
WORKER_ID = "worker-fast-1"
ATTEMPT_COUNT = 3


def make_job() -> ClaimedJob:
    return ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=ATTEMPT_COUNT,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )


class RecordingResultRepository:
    def __init__(
        self,
        session: object,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.error = error
        self.add_calls: list[TranscriptionResult] = []

    async def add(self, result: TranscriptionResult) -> None:
        self.events.append("add_result")
        self.add_calls.append(result)
        if self.error is not None:
            raise self.error


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
        self.mark_calls: list[tuple[UUID, str, int]] = []

    async def mark_completed(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> bool:
        self.events.append("mark_completed")
        self.mark_calls.append(
            (job_uuid, worker_id, expected_attempt_count),
        )
        if self.error is not None:
            raise self.error
        return self.outcome


def make_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result_error: Exception | None = None,
    mark_outcome: bool = True,
    mark_error: Exception | None = None,
    commit_error: Exception | None = None,
) -> tuple[
    TranscriptionCompletionService,
    RecordingResultRepository,
    RecordingJobRepository,
    list[str],
]:
    events: list[str] = []
    session = object()
    result_repository = RecordingResultRepository(
        session,
        events,
        error=result_error,
    )
    job_repository = RecordingJobRepository(
        session,
        events,
        outcome=mark_outcome,
        error=mark_error,
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

    def result_repository_factory(received_session: object):
        events.append("create_result_repository")
        assert received_session is session
        return result_repository

    def job_repository_factory(received_session: object):
        events.append("create_job_repository")
        assert received_session is session
        return job_repository

    monkeypatch.setattr(
        completion_module,
        "async_transaction",
        recording_transaction,
    )
    service = TranscriptionCompletionService(
        cast(AsyncSessionFactory, object()),
        job_repository_factory=job_repository_factory,
        result_repository_factory=result_repository_factory,
    )
    return service, result_repository, job_repository, events


async def test_complete_adds_result_then_marks_job_and_commits_same_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, result_repository, job_repository, events = make_service(monkeypatch)
    output = TranscriptionOutput(
        result={
            "text": "bonjour",
            "segments": [{"start": 0.0, "end": 1.0}],
        },
        speaker_count=2,
    )

    await service.complete(
        job=make_job(),
        worker_id=WORKER_ID,
        output=output,
    )

    assert events == [
        "transaction_begin",
        "create_result_repository",
        "create_job_repository",
        "add_result",
        "mark_completed",
        "transaction_commit",
    ]
    assert result_repository.session is job_repository.session
    assert len(result_repository.add_calls) == 1
    saved_result = result_repository.add_calls[0]
    assert saved_result.job_uuid == JOB_UUID
    assert saved_result.result == output.result
    assert saved_result.speaker_count == output.speaker_count
    assert job_repository.mark_calls == [
        (JOB_UUID, WORKER_ID, ATTEMPT_COUNT),
    ]


async def test_add_error_rolls_back_without_marking_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError("result insertion failed")
    service, result_repository, job_repository, events = make_service(
        monkeypatch,
        result_error=error,
    )

    with pytest.raises(RuntimeError) as raised:
        await service.complete(
            job=make_job(),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"text": "bonjour"}),
        )

    assert raised.value is error
    assert len(result_repository.add_calls) == 1
    assert job_repository.mark_calls == []
    assert events[-1] == "transaction_rollback"
    assert "transaction_commit" not in events


async def test_rejected_completion_is_raised_inside_transaction_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, result_repository, job_repository, events = make_service(
        monkeypatch,
        mark_outcome=False,
    )

    with pytest.raises(TranscriptionCompletionRejectedError) as raised:
        await service.complete(
            job=make_job(),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"text": "bonjour"}),
        )

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == ATTEMPT_COUNT
    assert len(result_repository.add_calls) == 1
    assert job_repository.mark_calls == [
        (JOB_UUID, WORKER_ID, ATTEMPT_COUNT),
    ]
    assert events[-1] == "transaction_rollback"
    assert "transaction_commit" not in events


async def test_sqlalchemy_error_is_translated_after_transaction_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_error = SQLAlchemyError("database unavailable")
    service, _, _, events = make_service(
        monkeypatch,
        mark_error=database_error,
    )

    with pytest.raises(TranscriptionCompletionError) as raised:
        await service.complete(
            job=make_job(),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"secret": "not logged"}),
        )

    assert not isinstance(raised.value, TranscriptionCompletionRejectedError)
    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.__cause__ is database_error
    assert "not logged" not in str(raised.value)
    assert events[-1] == "transaction_rollback"


async def test_sqlalchemy_commit_error_is_translated_with_its_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit_error = SQLAlchemyError("commit acknowledgement lost")
    service, _, _, events = make_service(
        monkeypatch,
        commit_error=commit_error,
    )

    with pytest.raises(TranscriptionCompletionError) as raised:
        await service.complete(
            job=make_job(),
            worker_id=WORKER_ID,
            output=TranscriptionOutput(result={"text": "bonjour"}),
        )

    assert raised.value.__cause__ is commit_error
    assert events[-1] == "transaction_rollback"
    assert "transaction_commit" not in events
