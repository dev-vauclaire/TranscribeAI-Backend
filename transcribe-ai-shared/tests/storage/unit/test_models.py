from datetime import UTC, datetime
from uuid import UUID

import pytest

from transcribe_ai_shared.storage import (
    AudioLocation,
    InvalidAudioLocationError,
    StorageScanResult,
    TranscriptionDirectory,
)


pytestmark = pytest.mark.unit

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")


def test_audio_location_exposes_its_canonical_uri_and_job_uuid() -> None:
    uri = f"{JOB_UUID}/input.wav"

    location = AudioLocation(uri)

    assert location.uri == uri
    assert str(location) == uri
    assert location.job_uuid == JOB_UUID


@pytest.mark.parametrize(
    "invalid_uri",
    [
        "../outside.wav",
        f"{JOB_UUID}/../../outside.wav",
        "/absolute/input.wav",
        f"{JOB_UUID}/INPUT.WAV",
        f"{JOB_UUID}/output.wav",
        f"{JOB_UUID}/input.wav/extra",
    ],
)
def test_audio_location_rejects_non_canonical_locations(
    invalid_uri: str,
) -> None:
    with pytest.raises(InvalidAudioLocationError):
        AudioLocation(invalid_uri)


def test_transcription_directory_accepts_safe_metadata() -> None:
    modified_at = datetime(2026, 8, 19, tzinfo=UTC)

    directory = TranscriptionDirectory(
        job_uuid=JOB_UUID,
        modified_at=modified_at,
        revision="revision",
    )

    assert directory.job_uuid == JOB_UUID
    assert directory.modified_at == modified_at
    assert directory.revision == "revision"


@pytest.mark.parametrize(
    ("job_uuid", "modified_at", "revision"),
    [
        ("../outside", datetime.now(UTC), "revision"),
        (JOB_UUID, datetime.now(), "revision"),
        (JOB_UUID, datetime.now(UTC), ""),
    ],
)
def test_transcription_directory_rejects_invalid_metadata(
    job_uuid: object,
    modified_at: datetime,
    revision: str,
) -> None:
    with pytest.raises(InvalidAudioLocationError):
        TranscriptionDirectory(
            job_uuid=job_uuid,  # type: ignore[arg-type]
            modified_at=modified_at,
            revision=revision,
        )


def test_storage_scan_result_defaults_to_no_scan_error() -> None:
    result = StorageScanResult(directories=())

    assert result.directories == ()
    assert result.invalid_entry_count == 0
    assert result.unsafe_entry_count == 0
    assert result.error_count == 0
