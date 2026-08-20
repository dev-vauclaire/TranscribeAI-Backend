from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
import pytest

from api.Services.get_transcription import GetTranscriptionResult
from api.config import ApiSettings
from api.create_app import create_app
from api.exceptions import TranscriptionNotFoundError, TranscriptionQueryError
from transcribe_ai_shared import JobStatus


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("c9493d58-748f-44b2-bb67-457519f67968")
UNKNOWN_JOB_UUID = UUID("2934b845-e49d-419c-94d4-c0d8d54c27a5")
TEST_SETTINGS = ApiSettings(
    host="127.0.0.1",
    port=8000,
    max_upload_size_bytes=100,
    ffprobe_path="ffprobe",
    ffprobe_timeout_seconds=30,
    fast_max_duration_seconds=Decimal("900"),
    batch_max_duration_seconds=Decimal("14400"),
)


class RecordingGetTranscriptionService:
    """Double du use case qui capture l'UUID transmis par le contrôleur."""

    def __init__(
        self,
        *,
        result: GetTranscriptionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[UUID] = []

    async def get(self, job_uuid: UUID) -> GetTranscriptionResult:
        self.calls.append(job_uuid)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("Le double doit recevoir un résultat ou une erreur.")
        return self.result


def build_app(service: RecordingGetTranscriptionService) -> FastAPI:
    return create_app(
        settings=TEST_SETTINGS,
        transcription_service=object(),  # type: ignore[arg-type]
        transcription_query_service=service,  # type: ignore[arg-type]
    )


async def get_transcription(app: FastAPI, job_uuid: str) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(f"/api/transcriptions/{job_uuid}")


@pytest.mark.parametrize(
    "job_status",
    [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.FAILED],
    ids=lambda status: status.value.lower(),
)
async def test_get_transcription_returns_non_completed_status_without_result(
    job_status: JobStatus,
) -> None:
    service = RecordingGetTranscriptionService(
        result=GetTranscriptionResult(
            job_uuid=JOB_UUID,
            status=job_status,
            result=None,
        )
    )

    response = await get_transcription(build_app(service), str(JOB_UUID))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "job_uuid": str(JOB_UUID),
        "status": job_status.value,
        "result": None,
    }
    assert service.calls == [JOB_UUID]


async def test_get_completed_transcription_returns_the_exact_json_result() -> None:
    transcription_result: dict[str, Any] = {
        "text": "Bonjour tout le monde.",
        "language": "fr",
        "segments": [
            {
                "start": 0.0,
                "end": 1.25,
                "text": "Bonjour tout le monde.",
            }
        ],
    }
    service = RecordingGetTranscriptionService(
        result=GetTranscriptionResult(
            job_uuid=JOB_UUID,
            status=JobStatus.COMPLETED,
            result=transcription_result,
        )
    )

    response = await get_transcription(build_app(service), str(JOB_UUID))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "job_uuid": str(JOB_UUID),
        "status": JobStatus.COMPLETED.value,
        "result": transcription_result,
    }
    assert service.calls == [JOB_UUID]


async def test_get_transcription_returns_404_when_job_does_not_exist() -> None:
    service = RecordingGetTranscriptionService(
        error=TranscriptionNotFoundError("Transcription introuvable."),
    )

    response = await get_transcription(build_app(service), str(UNKNOWN_JOB_UUID))

    assert response.status_code == 404
    assert response.json() == {"detail": "La transcription demandée n'existe pas."}
    assert service.calls == [UNKNOWN_JOB_UUID]


async def test_get_transcription_returns_500_without_exposing_database_error() -> None:
    service = RecordingGetTranscriptionService(
        error=TranscriptionQueryError("password authentication failed"),
    )

    response = await get_transcription(build_app(service), str(JOB_UUID))

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Le statut de la transcription est temporairement indisponible."
    }
    assert "password" not in response.text
    assert service.calls == [JOB_UUID]


async def test_get_transcription_rejects_invalid_uuid_before_calling_service() -> None:
    service = RecordingGetTranscriptionService(
        result=GetTranscriptionResult(
            job_uuid=JOB_UUID,
            status=JobStatus.QUEUED,
            result=None,
        )
    )

    response = await get_transcription(build_app(service), "not-a-uuid")

    assert response.status_code == 422
    assert service.calls == []
