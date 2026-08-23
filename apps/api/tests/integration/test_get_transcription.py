from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import ApiSettings
from api.create_app import create_app
from transcribe_ai_shared import (
    FileSystemAudioStorage,
    JobStatus,
    JobType,
    TranscriptionJob,
    TranscriptionResult,
    async_transaction,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
COMPLETED_RESULT: dict[str, Any] = {
    "text": "Bonjour le monde",
    "language": "fr",
    "segments": [
        {
            "start": 0.0,
            "end": 1.25,
            "text": "Bonjour le monde",
        }
    ],
}


async def _persist_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: JobStatus,
    result_payload: dict[str, Any] | None = None,
) -> UUID:
    """Insère un état métier cohérent et son éventuel résultat dans PostgreSQL."""
    job_uuid = uuid4()
    job_values: dict[str, Any] = {
        "job_uuid": job_uuid,
        "status": status,
        "job_type": JobType.FAST,
        "audio_uri": f"{job_uuid}/input.wav",
        "dispatch_required": status is JobStatus.QUEUED,
    }

    if status is JobStatus.PROCESSING:
        job_values.update(
            started_at=NOW,
            lease_owner="worker-fast-integration",
            lease_expires_at=NOW + timedelta(minutes=5),
        )
    elif status is JobStatus.FAILED:
        job_values.update(
            started_at=NOW - timedelta(minutes=1),
            completed_at=NOW,
            last_error="TRANSCRIPTION_FAILED",
        )
    elif status is JobStatus.COMPLETED:
        job_values.update(
            started_at=NOW - timedelta(minutes=1),
            completed_at=NOW,
        )

    async with async_transaction(session_factory) as session:
        session.add(TranscriptionJob(**job_values))
        await session.flush()
        if result_payload is not None:
            session.add(
                TranscriptionResult(
                    job_uuid=job_uuid,
                    result=result_payload,
                    speaker_count=1,
                    model_name="whisper",
                    model_version="large-v3",
                )
            )

    return job_uuid


@pytest_asyncio.fixture
async def api_client(
    tmp_path: Path,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings=ApiSettings(),
        storage=FileSystemAudioStorage(tmp_path),
        session_factory=async_session_factory,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.parametrize(
    "job_status",
    [
        JobStatus.QUEUED,
        JobStatus.PROCESSING,
        JobStatus.FAILED,
    ],
)
async def test_get_transcription_returns_non_completed_business_states(
    job_status: JobStatus,
    api_client: AsyncClient,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = await _persist_job(async_session_factory, status=job_status)

    response = await api_client.get(f"/api/transcriptions/{job_uuid}")

    assert response.status_code == 200
    assert response.json() == {
        "job_uuid": str(job_uuid),
        "status": job_status.value,
        "result": None,
    }
    assert response.headers["cache-control"] == "no-store"


async def test_get_completed_transcription_returns_persisted_jsonb_result(
    api_client: AsyncClient,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = await _persist_job(
        async_session_factory,
        status=JobStatus.COMPLETED,
        result_payload=COMPLETED_RESULT,
    )

    response = await api_client.get(f"/api/transcriptions/{job_uuid}")

    assert response.status_code == 200
    assert response.json() == {
        "job_uuid": str(job_uuid),
        "status": JobStatus.COMPLETED.value,
        "result": COMPLETED_RESULT,
    }
    assert response.headers["cache-control"] == "no-store"


async def test_get_completed_transcription_returns_500_without_durable_result(
    api_client: AsyncClient,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_uuid = await _persist_job(
        async_session_factory,
        status=JobStatus.COMPLETED,
    )

    response = await api_client.get(f"/api/transcriptions/{job_uuid}")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Le résultat de la transcription est indisponible."
    }


async def test_get_transcription_returns_404_for_unknown_job(
    api_client: AsyncClient,
) -> None:
    response = await api_client.get(f"/api/transcriptions/{uuid4()}")

    assert response.status_code == 404


async def test_get_transcription_rejects_invalid_uuid(
    api_client: AsyncClient,
) -> None:
    response = await api_client.get("/api/transcriptions/not-a-uuid")

    assert response.status_code == 422
