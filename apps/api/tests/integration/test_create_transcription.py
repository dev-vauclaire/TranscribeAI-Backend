from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
import wave
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import ApiSettings
from api.create_app import create_app
from transcribe_ai_shared import (
    FileSystemAudioStorage,
    JobRepository,
    JobStatus,
    JobType,
    TranscriptionJob,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
DEFAULT_FAST_MAX_DURATION_SECONDS = 60.0
DEFAULT_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS = 3_600.0


def _valid_wav_content(*, duration_seconds: float = 0.1) -> bytes:
    """Produit un petit WAV PCM dont la durée est déterministe pour ffprobe."""
    sample_rate = 16_000
    frame_count = round(sample_rate * duration_seconds)
    destination = BytesIO()
    with wave.open(destination, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frame_count)
    return destination.getvalue()


def _valid_audio_content(extension: str) -> bytes:
    """Encode un petit média réel dans chacun des conteneurs supportés."""
    wav_content = _valid_wav_content()
    if extension == "wav":
        return wav_content

    with TemporaryDirectory(prefix="transcribe-ai-api-test-") as directory:
        source = Path(directory) / "source.wav"
        destination = Path(directory) / f"encoded.{extension}"
        source.write_bytes(wav_content)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(source),
                str(destination),
            ],
            check=True,
            capture_output=True,
        )
        return destination.read_bytes()


def _api_settings(
    *,
    fast_max_duration_seconds: float = DEFAULT_FAST_MAX_DURATION_SECONDS,
    long_form_diarization_max_duration_seconds: float = (
        DEFAULT_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS
    ),
) -> ApiSettings:
    return ApiSettings(
        max_upload_size_bytes=MAX_UPLOAD_SIZE_BYTES,
        ffprobe_path="ffprobe",
        ffprobe_timeout_seconds=10,
        fast_max_duration_seconds=fast_max_duration_seconds,
        long_form_diarization_max_duration_seconds=(
            long_form_diarization_max_duration_seconds
        ),
    )


@asynccontextmanager
async def _client_for(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


async def _count_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(TranscriptionJob))
    assert count is not None
    return count


class _FailingRepositoryFactory:
    """Construit le repository fautif et enregistre le chemin réellement exercé."""

    def __init__(self) -> None:
        self.real_add_completed_count = 0
        self.invalid_statement_executed_count = 0

    def __call__(self, session: AsyncSession) -> "_FailingAfterAddJobRepository":
        return _FailingAfterAddJobRepository(session, recorder=self)


class _FailingAfterAddJobRepository:
    """Déclenche une erreur PostgreSQL après le vrai flush du job."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        recorder: _FailingRepositoryFactory,
    ) -> None:
        self._session = session
        self._delegate = JobRepository(session)
        self._recorder = recorder

    async def add(self, job: TranscriptionJob) -> None:
        await self._delegate.add(job)
        self._recorder.real_add_completed_count += 1
        self._recorder.invalid_statement_executed_count += 1
        await self._session.execute(text("SELECT 1 / 0"))

    async def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        return await self._delegate.get_by_uuid(job_uuid)


@pytest.fixture
def audio_storage(tmp_path: Path) -> FileSystemAudioStorage:
    return FileSystemAudioStorage(tmp_path)


@pytest.fixture
def api_app(
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> FastAPI:
    return create_app(
        settings=_api_settings(),
        storage=audio_storage,
        session_factory=async_session_factory,
    )


@pytest_asyncio.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client_for(api_app) as client:
        yield client


@pytest.mark.parametrize(
    ("job_type", "extension", "content_type"),
    [
        (JobType.FAST, "wav", "audio/wav"),
        (JobType.LONG_FORM_DIARIZATION, "mp3", "audio/mpeg"),
        (JobType.FAST, "ogg", "audio/ogg"),
        (JobType.LONG_FORM_DIARIZATION, "m4a", "audio/mp4"),
    ],
)
async def test_post_transcriptions_commits_job_and_publishes_complete_audio(
    job_type: JobType,
    extension: str,
    content_type: str,
    api_client: AsyncClient,
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    audio_content = _valid_audio_content(extension)

    response = await api_client.post(
        "/api/transcriptions",
        data={"type": job_type.value},
        files={
            "audio_file": (
                f"recording.{extension}",
                audio_content,
                content_type,
            )
        },
    )

    assert response.status_code == 202
    job_uuid = UUID(response.json()["job_uuid"])
    assert response.headers["location"] == f"/api/transcriptions/{job_uuid}"
    assert response.json() == {
        "job_uuid": str(job_uuid),
        "status": JobStatus.QUEUED.value,
    }

    async with async_session_factory() as session:
        repository = JobRepository(session)
        saved_job = await repository.get_by_uuid(job_uuid)
        dispatchable_jobs = await repository.find_jobs_requiring_dispatch(limit=10)

    assert saved_job is not None
    expected_audio_uri = f"{job_uuid}/input.{extension}"
    assert saved_job.status is JobStatus.QUEUED
    assert saved_job.job_type is job_type
    assert saved_job.audio_uri == expected_audio_uri
    assert saved_job.dispatch_required is True
    assert saved_job.last_dispatched_at is None
    assert [job.job_uuid for job in dispatchable_jobs] == [job_uuid]

    stored_audio = audio_storage.root / expected_audio_uri
    assert stored_audio.is_file()
    assert stored_audio.read_bytes() == audio_content
    assert sorted(path.name for path in stored_audio.parent.iterdir()) == [
        f"input.{extension}"
    ]
    assert list(audio_storage.root.rglob(".upload-*.tmp")) == []


async def test_post_transcriptions_stays_available_when_redis_is_down(
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis reste hors du chemin critique HTTP : seul PostgreSQL est écrit."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    app = create_app(
        settings=_api_settings(),
        storage=audio_storage,
        session_factory=async_session_factory,
    )

    async with _client_for(app) as client:
        response = await client.post(
            "/api/transcriptions",
            data={"type": JobType.FAST.value},
            files={
                "audio_file": (
                    "recording.wav",
                    _valid_wav_content(),
                    "audio/wav",
                )
            },
        )

    assert response.status_code == 202
    job_uuid = UUID(response.json()["job_uuid"])

    async with async_session_factory() as session:
        saved_job = await JobRepository(session).get_by_uuid(job_uuid)

    assert saved_job is not None
    assert saved_job.status is JobStatus.QUEUED
    assert saved_job.dispatch_required is True
    assert saved_job.last_dispatched_at is None


async def test_post_transcriptions_rejects_declared_wav_with_non_audio_content(
    api_client: AsyncClient,
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_spy = MagicMock(wraps=audio_storage.save)
    monkeypatch.setattr(audio_storage, "save", save_spy)

    response = await api_client.post(
        "/api/transcriptions",
        data={"type": JobType.FAST.value},
        files={
            "audio_file": (
                "not-an-audio.wav",
                b"this is not a WAV file",
                "audio/wav",
            )
        },
    )

    assert response.status_code == 422
    save_spy.assert_called_once()
    assert await _count_jobs(async_session_factory) == 0
    assert list(audio_storage.root.iterdir()) == []


async def test_post_transcriptions_rejects_real_mp3_disguised_as_wav(
    api_client: AsyncClient,
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await api_client.post(
        "/api/transcriptions",
        data={"type": JobType.FAST.value},
        files={
            "audio_file": (
                "disguised.wav",
                _valid_audio_content("mp3"),
                "audio/wav",
            )
        },
    )

    assert response.status_code == 415
    assert await _count_jobs(async_session_factory) == 0
    assert list(audio_storage.root.iterdir()) == []


async def test_post_transcriptions_rejects_unsupported_mime_before_storage(
    api_client: AsyncClient,
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_spy = MagicMock(wraps=audio_storage.save)
    monkeypatch.setattr(audio_storage, "save", save_spy)

    response = await api_client.post(
        "/api/transcriptions",
        data={"type": JobType.FAST.value},
        files={
            "audio_file": (
                "recording.wav",
                _valid_wav_content(),
                "text/plain",
            )
        },
    )

    assert response.status_code == 415
    save_spy.assert_not_called()
    assert await _count_jobs(async_session_factory) == 0
    assert list(audio_storage.root.iterdir()) == []


async def test_post_transcriptions_rejects_audio_above_fast_duration_limit(
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_spy = MagicMock(wraps=audio_storage.save)
    monkeypatch.setattr(audio_storage, "save", save_spy)
    app = create_app(
        settings=_api_settings(fast_max_duration_seconds=0.05),
        storage=audio_storage,
        session_factory=async_session_factory,
    )

    async with _client_for(app) as client:
        response = await client.post(
            "/api/transcriptions",
            data={"type": JobType.FAST.value},
            files={
                "audio_file": (
                    "recording.wav",
                    _valid_wav_content(duration_seconds=0.1),
                    "audio/wav",
                )
            },
        )

    assert response.status_code == 422
    save_spy.assert_called_once()
    assert await _count_jobs(async_session_factory) == 0
    assert list(audio_storage.root.iterdir()) == []


async def test_post_transcriptions_rolls_back_and_cleans_audio_on_database_error(
    audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_spy = MagicMock(wraps=audio_storage.save)
    monkeypatch.setattr(audio_storage, "save", save_spy)
    failing_repository_factory = _FailingRepositoryFactory()
    app = create_app(
        settings=_api_settings(),
        storage=audio_storage,
        session_factory=async_session_factory,
        repository_factory=failing_repository_factory,
    )

    async with _client_for(app) as client:
        response = await client.post(
            "/api/transcriptions",
            data={"type": JobType.FAST.value},
            files={
                "audio_file": (
                    "recording.wav",
                    _valid_wav_content(),
                    "audio/wav",
                )
            },
        )

    assert response.status_code == 503
    assert failing_repository_factory.real_add_completed_count == 1
    assert failing_repository_factory.invalid_statement_executed_count == 1
    save_spy.assert_called_once()
    assert await _count_jobs(async_session_factory) == 0
    assert list(audio_storage.root.iterdir()) == []
