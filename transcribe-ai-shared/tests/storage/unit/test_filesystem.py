from datetime import UTC, datetime
from io import BytesIO
import os
from pathlib import Path
from uuid import UUID

import pytest

from transcribe_ai_shared.storage import (
    AudioAlreadyExistsError,
    AudioDirectoryChangedError,
    AudioLocation,
    AudioNotFoundError,
    FileSystemAudioStorage,
    InvalidAudioLocationError,
)


pytestmark = pytest.mark.unit

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
AUDIO_CONTENT = b"audio content"


class FailingAudioStream(BytesIO):
    """Émet un premier fragment puis simule l'échec de la source."""

    def __init__(self, content: bytes):
        super().__init__(content)
        self._first_read = True

    def read(self, size: int = -1) -> bytes:
        if not self._first_read:
            raise OSError("source unavailable")
        self._first_read = False
        return super().read(min(size, 4))


class PublicationProbeStream(BytesIO):
    """Vérifie que le chemin final reste absent pendant la copie."""

    def __init__(self, content: bytes, final_path: Path):
        super().__init__(content)
        self._final_path = final_path

    def read(self, size: int = -1) -> bytes:
        assert self._final_path.exists() is False
        return super().read(size)


def test_save_writes_audio_in_job_directory_and_returns_location(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)

    location = storage.save(
        JOB_UUID,
        BytesIO(AUDIO_CONTENT),
        extension=".WAV",
    )

    expected_uri = f"{JOB_UUID}/input.wav"
    assert location == AudioLocation(expected_uri)
    assert str(location) == expected_uri
    assert location.job_uuid == JOB_UUID
    assert (tmp_path / expected_uri).read_bytes() == AUDIO_CONTENT


def test_save_publishes_only_after_the_source_is_fully_copied(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    final_path = tmp_path / str(JOB_UUID) / "input.wav"

    location = storage.save(
        JOB_UUID,
        PublicationProbeStream(AUDIO_CONTENT, final_path),
        extension="wav",
    )

    assert location == AudioLocation(f"{JOB_UUID}/input.wav")
    assert final_path.read_bytes() == AUDIO_CONTENT


def test_save_cleans_a_failed_temporary_file_and_allows_retry(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)

    with pytest.raises(OSError, match="source unavailable"):
        storage.save(
            JOB_UUID,
            FailingAudioStream(AUDIO_CONTENT),
            extension="wav",
        )

    assert list(tmp_path.iterdir()) == []
    location = storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")
    assert (tmp_path / location.uri).read_bytes() == AUDIO_CONTENT


def test_save_cleans_temporary_state_when_atomic_publication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = FileSystemAudioStorage(tmp_path)

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("rename unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="rename unavailable"):
        storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")

    assert list(tmp_path.iterdir()) == []


def test_save_cleans_temporary_state_when_file_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = FileSystemAudioStorage(tmp_path)

    def fail_fsync(file_descriptor: int) -> None:
        raise OSError("file sync unavailable")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="file sync unavailable"):
        storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")

    assert list(tmp_path.iterdir()) == []


def test_open_returns_readable_binary_stream(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    location = storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")

    with storage.open(location) as audio_stream:
        assert audio_stream.read() == AUDIO_CONTENT
        assert audio_stream.closed is False

    assert audio_stream.closed is True


def test_delete_removes_audio_and_returns_none(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    location = storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")

    result = storage.delete(location)

    assert result is None
    assert (tmp_path / location.uri).exists() is False


def test_delete_is_idempotent_when_audio_does_not_exist(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    missing_location = AudioLocation(f"{JOB_UUID}/input.wav")

    assert storage.delete(missing_location) is None
    assert storage.delete(missing_location) is None


@pytest.mark.parametrize("second_extension", ["wav", "mp3"])
def test_save_rejects_collision_without_overwriting_existing_audio(
    tmp_path: Path,
    second_extension: str,
):
    storage = FileSystemAudioStorage(tmp_path)
    first_location = storage.save(
        JOB_UUID,
        BytesIO(AUDIO_CONTENT),
        extension="wav",
    )

    with pytest.raises(AudioAlreadyExistsError):
        storage.save(
            JOB_UUID,
            BytesIO(b"replacement audio"),
            extension=second_extension,
        )

    job_directory = tmp_path / str(JOB_UUID)
    assert (tmp_path / first_location.uri).read_bytes() == AUDIO_CONTENT
    assert [path.name for path in job_directory.iterdir()] == ["input.wav"]


def test_open_raises_when_audio_does_not_exist(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    missing_location = AudioLocation(f"{JOB_UUID}/input.wav")

    with pytest.raises(AudioNotFoundError):
        with storage.open(missing_location):
            pass


@pytest.mark.parametrize(
    "extension",
    [
        "../wav",
        "wav/../../outside",
        "/absolute/wav",
    ],
)
def test_save_rejects_malicious_extension(tmp_path: Path, extension: str):
    storage = FileSystemAudioStorage(tmp_path / "storage")

    with pytest.raises(InvalidAudioLocationError):
        storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension=extension)

    assert list((tmp_path / "storage").rglob("*")) == []


@pytest.mark.parametrize("operation", ["open", "delete"])
def test_storage_rejects_symlink_outside_root(tmp_path: Path, operation: str):
    root = tmp_path / "storage"
    root.mkdir()
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    outside_file = outside_directory / "input.wav"
    outside_file.write_bytes(b"must be preserved")
    (root / str(JOB_UUID)).symlink_to(outside_directory, target_is_directory=True)
    storage = FileSystemAudioStorage(root)
    location = AudioLocation(f"{JOB_UUID}/input.wav")

    with pytest.raises(InvalidAudioLocationError):
        if operation == "open":
            with storage.open(location):
                pass
        else:
            storage.delete(location)

    assert outside_file.read_bytes() == b"must be preserved"


def test_scan_returns_canonical_uuid_metadata_with_utc_mtime(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    location = storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")
    expected_modified_at = datetime(2025, 1, 1, tzinfo=UTC)
    timestamp = expected_modified_at.timestamp()
    os.utime(tmp_path / location.uri, (timestamp, timestamp))
    os.utime(tmp_path / str(JOB_UUID), (timestamp, timestamp))

    result = storage.scan_transcription_directories()

    assert result.invalid_entry_count == 0
    assert result.unsafe_entry_count == 0
    assert result.error_count == 0
    assert len(result.directories) == 1
    directory = result.directories[0]
    assert directory.job_uuid == JOB_UUID
    assert directory.modified_at == expected_modified_at
    assert directory.revision


def test_scan_ignores_root_files_and_counts_invalid_uuid_directory(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    (tmp_path / ".upload-abandoned.tmp").write_bytes(b"partial")
    invalid_directory = tmp_path / "not-a-job-uuid"
    invalid_directory.mkdir()
    (invalid_directory / "input.wav").write_bytes(AUDIO_CONTENT)

    result = storage.scan_transcription_directories()

    assert result.directories == ()
    assert result.invalid_entry_count == 1
    assert result.unsafe_entry_count == 0
    assert result.error_count == 0


def test_scan_and_delete_support_an_abandoned_temporary_upload(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    job_directory = tmp_path / str(JOB_UUID)
    job_directory.mkdir()
    temporary_audio = job_directory / f".upload-{'a' * 32}.tmp"
    temporary_audio.write_bytes(AUDIO_CONTENT)

    result = storage.scan_transcription_directories()

    assert result.invalid_entry_count == 0
    assert result.unsafe_entry_count == 0
    assert result.error_count == 0
    assert len(result.directories) == 1
    assert result.directories[0].job_uuid == JOB_UUID
    assert storage.delete_transcription_directory(result.directories[0]) is True
    assert job_directory.exists() is False


def test_scan_refuses_root_and_nested_symlinks(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path / "storage")
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "input.wav").write_bytes(b"must be preserved")
    root = tmp_path / "storage"
    (root / str(JOB_UUID)).symlink_to(outside_directory, target_is_directory=True)

    nested_job_uuid = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    nested_directory = root / str(nested_job_uuid)
    nested_directory.mkdir()
    (nested_directory / "input.wav").symlink_to(outside_directory / "input.wav")

    result = storage.scan_transcription_directories()

    assert result.directories == ()
    assert result.invalid_entry_count == 0
    assert result.unsafe_entry_count == 2
    assert (outside_directory / "input.wav").read_bytes() == b"must be preserved"


@pytest.mark.parametrize("operation", ["open", "delete"])
def test_storage_rejects_a_nested_audio_symlink(
    tmp_path: Path,
    operation: str,
):
    root = tmp_path / "storage"
    storage = FileSystemAudioStorage(root)
    job_directory = root / str(JOB_UUID)
    job_directory.mkdir()
    outside_audio = tmp_path / "outside.wav"
    outside_audio.write_bytes(b"must be preserved")
    (job_directory / "input.wav").symlink_to(outside_audio)
    location = AudioLocation(f"{JOB_UUID}/input.wav")

    with pytest.raises(InvalidAudioLocationError):
        if operation == "open":
            with storage.open(location):
                pass
        else:
            storage.delete(location)

    assert outside_audio.read_bytes() == b"must be preserved"


def test_delete_transcription_directory_is_confined_and_idempotent(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")
    directory = storage.scan_transcription_directories().directories[0]

    assert storage.delete_transcription_directory(directory) is True
    assert storage.delete_transcription_directory(directory) is False
    assert (tmp_path / str(JOB_UUID)).exists() is False


def test_delete_transcription_directory_rejects_a_stale_revision(tmp_path: Path):
    storage = FileSystemAudioStorage(tmp_path)
    location = storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")
    directory = storage.scan_transcription_directories().directories[0]
    (tmp_path / location.uri).write_bytes(b"changed after scan")

    with pytest.raises(AudioDirectoryChangedError):
        storage.delete_transcription_directory(directory)

    assert (tmp_path / location.uri).read_bytes() == b"changed after scan"


def test_delete_transcription_directory_refuses_a_replacement_symlink(
    tmp_path: Path,
):
    storage = FileSystemAudioStorage(tmp_path / "storage")
    storage.save(JOB_UUID, BytesIO(AUDIO_CONTENT), extension="wav")
    directory = storage.scan_transcription_directories().directories[0]
    root = tmp_path / "storage"
    original_directory = root / str(JOB_UUID)
    moved_directory = tmp_path / "original"
    original_directory.rename(moved_directory)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    outside_file = outside_directory / "input.wav"
    outside_file.write_bytes(b"must be preserved")
    original_directory.symlink_to(outside_directory, target_is_directory=True)

    with pytest.raises(InvalidAudioLocationError):
        storage.delete_transcription_directory(directory)

    assert outside_file.read_bytes() == b"must be preserved"
    assert (moved_directory / "input.wav").read_bytes() == AUDIO_CONTENT
