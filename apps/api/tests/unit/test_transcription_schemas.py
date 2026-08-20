from math import nan
from uuid import UUID

from pydantic import ValidationError
import pytest

from api.Schemas import TranscriptionStatusResponse
from transcribe_ai_shared import JobStatus


pytestmark = pytest.mark.unit

JOB_UUID = UUID("c9493d58-748f-44b2-bb67-457519f67968")


def test_completed_status_requires_a_result() -> None:
    with pytest.raises(ValidationError):
        TranscriptionStatusResponse(
            job_uuid=JOB_UUID,
            status=JobStatus.COMPLETED,
            result=None,
        )


@pytest.mark.parametrize(
    "job_status",
    [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.FAILED],
)
def test_non_completed_status_rejects_a_result(job_status: JobStatus) -> None:
    with pytest.raises(ValidationError):
        TranscriptionStatusResponse(
            job_uuid=JOB_UUID,
            status=job_status,
            result={"text": "résultat prématuré"},
        )


@pytest.mark.parametrize(
    "invalid_result",
    [
        {"confidence": nan},
        {"not_json": {"set values"}},
    ],
)
def test_status_response_rejects_non_json_values(invalid_result: object) -> None:
    with pytest.raises(ValidationError):
        TranscriptionStatusResponse(
            job_uuid=JOB_UUID,
            status=JobStatus.COMPLETED,
            result=invalid_result,  # type: ignore[arg-type]
        )
