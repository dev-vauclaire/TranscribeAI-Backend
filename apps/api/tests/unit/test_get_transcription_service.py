from dataclasses import dataclass
from types import TracebackType
from unittest.mock import AsyncMock, MagicMock, create_autospec
from uuid import UUID

import pytest

from api.Services.get_transcription import GetTranscriptionService
from api.exceptions import TranscriptionNotFoundError, TranscriptionQueryError
from transcribe_ai_shared import (
    JobRepository,
    JobStatus,
    JobType,
    ResultRepository,
    TranscriptionJob,
    TranscriptionResult,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("c9493d58-748f-44b2-bb67-457519f67968")


class RecordingSessionContext:
    """Double du cycle de vie d'une session de lecture asynchrone."""

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
    """Crée une session observable sans PostgreSQL ni transaction réelle."""

    def __init__(self, context: RecordingSessionContext) -> None:
        self.context = context
        self.call_count = 0

    def __call__(self) -> RecordingSessionContext:
        self.call_count += 1
        return self.context


@dataclass
class ServiceHarness:
    service: GetTranscriptionService
    session_context: RecordingSessionContext
    session_factory: RecordingSessionFactory
    job_repository: MagicMock
    result_repository: MagicMock
    job_repository_factory: MagicMock
    result_repository_factory: MagicMock


def make_job(status: JobStatus) -> TranscriptionJob:
    return TranscriptionJob(
        job_uuid=JOB_UUID,
        status=status,
        job_type=JobType.FAST,
        audio_uri=f"{JOB_UUID}/input.wav",
    )


def build_service_harness(
    *,
    job: TranscriptionJob | None = None,
    transcription_result: TranscriptionResult | None = None,
) -> ServiceHarness:
    session_context = RecordingSessionContext()
    session_factory = RecordingSessionFactory(session_context)
    job_repository = create_autospec(JobRepository, instance=True)
    job_repository.get_by_uuid = AsyncMock(return_value=job)
    result_repository = create_autospec(ResultRepository, instance=True)
    result_repository.get_by_job_uuid = AsyncMock(return_value=transcription_result)
    job_repository_factory = MagicMock(return_value=job_repository)
    result_repository_factory = MagicMock(return_value=result_repository)

    return ServiceHarness(
        service=GetTranscriptionService(
            session_factory=session_factory,  # type: ignore[arg-type]
            job_repository_factory=job_repository_factory,
            result_repository_factory=result_repository_factory,
        ),
        session_context=session_context,
        session_factory=session_factory,
        job_repository=job_repository,
        result_repository=result_repository,
        job_repository_factory=job_repository_factory,
        result_repository_factory=result_repository_factory,
    )


@pytest.mark.parametrize(
    "job_status",
    [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.FAILED],
    ids=lambda status: status.value.lower(),
)
async def test_get_returns_status_without_querying_result_before_completion(
    job_status: JobStatus,
) -> None:
    harness = build_service_harness(job=make_job(job_status))

    result = await harness.service.get(JOB_UUID)

    assert result.job_uuid == JOB_UUID
    assert result.status is job_status
    assert result.result is None
    harness.job_repository.get_by_uuid.assert_awaited_once_with(JOB_UUID)
    harness.job_repository_factory.assert_called_once_with(
        harness.session_context.session
    )
    harness.result_repository_factory.assert_not_called()
    assert harness.session_factory.call_count == 1
    assert harness.session_context.entered is True
    assert harness.session_context.exited is True


async def test_get_completed_job_returns_its_json_result() -> None:
    payload = {
        "text": "Bonjour tout le monde.",
        "segments": [{"start": 0.0, "end": 1.25, "text": "Bonjour."}],
    }
    transcription_result = TranscriptionResult(
        job_uuid=JOB_UUID,
        result=payload,
    )
    harness = build_service_harness(
        job=make_job(JobStatus.COMPLETED),
        transcription_result=transcription_result,
    )

    result = await harness.service.get(JOB_UUID)

    assert result.job_uuid == JOB_UUID
    assert result.status is JobStatus.COMPLETED
    assert result.result == payload
    harness.result_repository.get_by_job_uuid.assert_awaited_once_with(JOB_UUID)
    harness.result_repository_factory.assert_called_once_with(
        harness.session_context.session
    )


async def test_get_raises_not_found_when_job_does_not_exist() -> None:
    harness = build_service_harness(job=None)

    with pytest.raises(TranscriptionNotFoundError):
        await harness.service.get(JOB_UUID)

    harness.job_repository.get_by_uuid.assert_awaited_once_with(JOB_UUID)
    harness.result_repository_factory.assert_not_called()
    assert harness.session_context.exited is True


async def test_get_wraps_database_failure_as_query_error() -> None:
    harness = build_service_harness(job=make_job(JobStatus.QUEUED))
    database_error = RuntimeError("database connection lost")
    harness.job_repository.get_by_uuid.side_effect = database_error

    with pytest.raises(TranscriptionQueryError) as captured:
        await harness.service.get(JOB_UUID)

    assert captured.value.__cause__ is database_error
    harness.result_repository_factory.assert_not_called()
    assert harness.session_context.exited is True


async def test_get_rejects_completed_job_without_durable_result() -> None:
    harness = build_service_harness(
        job=make_job(JobStatus.COMPLETED),
        transcription_result=None,
    )

    with pytest.raises(TranscriptionQueryError):
        await harness.service.get(JOB_UUID)

    harness.result_repository.get_by_job_uuid.assert_awaited_once_with(JOB_UUID)
    assert harness.session_context.exited is True


async def test_get_wraps_result_repository_failure_as_query_error() -> None:
    harness = build_service_harness(
        job=make_job(JobStatus.COMPLETED),
        transcription_result=None,
    )
    database_error = RuntimeError("result query failed")
    harness.result_repository.get_by_job_uuid.side_effect = database_error

    with pytest.raises(TranscriptionQueryError) as captured:
        await harness.service.get(JOB_UUID)

    assert captured.value.__cause__ is database_error
    harness.result_repository.get_by_job_uuid.assert_awaited_once_with(JOB_UUID)
    assert harness.session_context.exited is True
