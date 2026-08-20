from decimal import Decimal
from io import BytesIO
from typing import BinaryIO
from uuid import UUID

from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient, Response
import pytest

from api.Services.create_transcription import CreateTranscriptionResult
from api.Validators.upload_metadata import (
    UploadMetadataValidator,
    ValidatedUploadMetadata,
)
from api.config import ApiSettings
from api.create_app import create_app
from api.exceptions import (
    AudioStorageUnavailableError,
    AudioTooLongError,
    EmptyUploadError,
    InvalidAudioFileError,
    MediaProbeUnavailableError,
    MissingUploadFilenameError,
    TranscriptionPersistenceError,
    UnsupportedAudioCodecError,
    UnsupportedAudioFormatError,
    UnsupportedDeclaredMediaTypeError,
    UnsupportedFileExtensionError,
    UploadSizeUnavailableError,
    UploadTooLargeError,
)
from transcribe_ai_shared import JobStatus, JobType


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("c9493d58-748f-44b2-bb67-457519f67968")
AUDIO_CONTENT = b"audio payload"
MAX_UPLOAD_SIZE_BYTES = 100
TEST_SETTINGS = ApiSettings(
    host="127.0.0.1",
    port=8000,
    max_upload_size_bytes=MAX_UPLOAD_SIZE_BYTES,
    ffprobe_path="ffprobe",
    ffprobe_timeout_seconds=30,
    fast_max_duration_seconds=Decimal("900"),
    batch_max_duration_seconds=Decimal("14400"),
)


class RecordingCreateTranscriptionService:
    """Double du use case qui capture le contrat transmis par le controller."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.calls: list[dict[str, object]] = []

    async def create(
        self,
        *,
        source: BinaryIO,
        extension: str,
        job_type: JobType,
    ) -> CreateTranscriptionResult:
        if self.events is not None:
            self.events.append("service")
        self.calls.append(
            {
                "content": source.read(),
                "extension": extension,
                "job_type": job_type,
            }
        )
        if self.error is not None:
            raise self.error
        return CreateTranscriptionResult(
            job_uuid=JOB_UUID,
            status=JobStatus.QUEUED,
        )


class RecordingUploadMetadataValidator:
    """Double utilisé pour observer l'ordre ou simuler une métadonnée rare."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.calls: list[UploadFile] = []

    def validate(self, upload: UploadFile) -> ValidatedUploadMetadata:
        if self.events is not None:
            self.events.append("prevalidation")
        self.calls.append(upload)
        if self.error is not None:
            raise self.error
        return ValidatedUploadMetadata(
            extension="wav",
            size_bytes=upload.size or 1,
            declared_content_type="audio/wav",
        )


def build_app(
    service: RecordingCreateTranscriptionService,
    validator: UploadMetadataValidator | RecordingUploadMetadataValidator,
) -> FastAPI:
    return create_app(
        settings=TEST_SETTINGS,
        transcription_service=service,  # type: ignore[arg-type]
        upload_metadata_validator=validator,  # type: ignore[arg-type]
    )


async def post_transcription(
    app: FastAPI,
    *,
    transcription_type: str = JobType.FAST.value,
    filename: str = "meeting.wav",
    content: bytes = AUDIO_CONTENT,
    content_type: str = "audio/wav",
) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.post(
            "/transcriptions",
            data={"type": transcription_type},
            files={"audio_file": (filename, BytesIO(content), content_type)},
        )


@pytest.mark.parametrize("job_type", [JobType.FAST, JobType.BATCH])
async def test_post_transcriptions_returns_202_location_and_queued_job(
    job_type: JobType,
) -> None:
    service = RecordingCreateTranscriptionService()
    app = build_app(service, UploadMetadataValidator(MAX_UPLOAD_SIZE_BYTES))

    response = await post_transcription(
        app,
        transcription_type=job_type.value,
    )

    assert response.status_code == 202
    assert response.headers["location"] == f"/transcriptions/{JOB_UUID}"
    assert response.json() == {
        "job_uuid": str(JOB_UUID),
        "status": JobStatus.QUEUED.value,
    }
    assert service.calls == [
        {
            "content": AUDIO_CONTENT,
            "extension": "wav",
            "job_type": job_type,
        }
    ]


async def test_controller_prevalidates_upload_before_calling_service() -> None:
    events: list[str] = []
    validator = RecordingUploadMetadataValidator(events=events)
    service = RecordingCreateTranscriptionService(events=events)

    response = await post_transcription(build_app(service, validator))

    assert response.status_code == 202
    assert events == ["prevalidation", "service"]
    assert len(validator.calls) == 1
    assert len(service.calls) == 1


@pytest.mark.parametrize(
    ("max_size_bytes", "filename", "content", "content_type", "expected_status"),
    [
        pytest.param(4, "meeting.wav", b"12345", "audio/wav", 413, id="too-large"),
        pytest.param(
            MAX_UPLOAD_SIZE_BYTES,
            "meeting.flac",
            AUDIO_CONTENT,
            "audio/flac",
            415,
            id="unsupported-extension",
        ),
        pytest.param(
            MAX_UPLOAD_SIZE_BYTES,
            "meeting.wav",
            AUDIO_CONTENT,
            "audio/mpeg",
            415,
            id="mismatched-declared-mime",
        ),
        pytest.param(
            MAX_UPLOAD_SIZE_BYTES,
            "",
            AUDIO_CONTENT,
            "audio/wav",
            422,
            id="missing-filename",
        ),
        pytest.param(
            MAX_UPLOAD_SIZE_BYTES,
            "meeting.wav",
            b"",
            "audio/wav",
            422,
            id="empty-upload",
        ),
    ],
)
async def test_post_transcriptions_maps_real_upload_metadata_errors(
    max_size_bytes: int,
    filename: str,
    content: bytes,
    content_type: str,
    expected_status: int,
) -> None:
    service = RecordingCreateTranscriptionService()
    validator = UploadMetadataValidator(max_size_bytes)

    response = await post_transcription(
        build_app(service, validator),
        filename=filename,
        content=content,
        content_type=content_type,
    )

    assert response.status_code == expected_status
    assert service.calls == []


@pytest.mark.parametrize(
    ("validator_error", "expected_status"),
    [
        (MissingUploadFilenameError("missing filename"), 422),
        (UploadSizeUnavailableError("size unavailable"), 422),
        (
            UploadTooLargeError(
                actual_size_bytes=101,
                max_size_bytes=100,
            ),
            413,
        ),
        (UnsupportedFileExtensionError("unsupported extension"), 415),
        (UnsupportedDeclaredMediaTypeError("unsupported MIME"), 415),
        (EmptyUploadError("empty upload"), 422),
    ],
)
async def test_post_transcriptions_maps_all_upload_validation_exceptions(
    validator_error: Exception,
    expected_status: int,
) -> None:
    service = RecordingCreateTranscriptionService()
    validator = RecordingUploadMetadataValidator(error=validator_error)

    response = await post_transcription(build_app(service, validator))

    assert response.status_code == expected_status
    assert service.calls == []


@pytest.mark.parametrize(
    ("service_error", "expected_status"),
    [
        (UnsupportedAudioFormatError("unsupported actual format"), 415),
        (UnsupportedAudioCodecError("unsupported actual codec"), 415),
        (InvalidAudioFileError("invalid audio"), 422),
        (
            AudioTooLongError(
                actual_duration_seconds=Decimal("901"),
                max_duration_seconds=Decimal("900"),
                job_type=JobType.FAST,
            ),
            422,
        ),
        (AudioStorageUnavailableError("storage unavailable"), 503),
        (MediaProbeUnavailableError("probe unavailable"), 503),
        (TranscriptionPersistenceError("database unavailable"), 503),
    ],
)
async def test_post_transcriptions_maps_service_exceptions(
    service_error: Exception,
    expected_status: int,
) -> None:
    service = RecordingCreateTranscriptionService(error=service_error)
    validator = UploadMetadataValidator(MAX_UPLOAD_SIZE_BYTES)

    response = await post_transcription(build_app(service, validator))

    assert response.status_code == expected_status
    assert len(service.calls) == 1


async def test_post_transcriptions_rejects_an_unknown_type_before_controller() -> None:
    service = RecordingCreateTranscriptionService()
    validator = RecordingUploadMetadataValidator()

    response = await post_transcription(
        build_app(service, validator),
        transcription_type="UNKNOWN",
    )

    assert response.status_code == 422
    assert validator.calls == []
    assert service.calls == []
