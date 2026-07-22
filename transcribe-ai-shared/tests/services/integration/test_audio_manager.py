from pathlib import Path

import pytest

from transcribe_ai_shared.services.audio_manager import AudioManager, WrongAudioPathError


pytestmark = pytest.mark.integration


class UploadedAudio:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content

    def save(self, destination: str):
        Path(destination).write_bytes(self.content)


def test_initialization_creates_audio_directory(tmp_path):
    audio_directory = tmp_path / "nested" / "audio"

    manager = AudioManager(str(audio_directory))

    assert audio_directory.is_dir()
    assert manager.folder_path == str(audio_directory)


def test_initialization_preserves_existing_directory_content(tmp_path):
    existing_file = tmp_path / "existing.wav"
    existing_file.write_bytes(b"existing audio")

    AudioManager(str(tmp_path))

    assert existing_file.read_bytes() == b"existing audio"


def test_save_audio_writes_file_and_returns_its_path(tmp_path):
    manager = AudioManager(str(tmp_path))
    upload = UploadedAudio("job-123.wav", b"audio content")

    saved_path = manager.save_audio(upload)

    assert saved_path == "job-123.wav"
    assert (tmp_path / saved_path).read_bytes() == b"audio content"


def test_delete_audio_removes_existing_file(tmp_path):
    audio_file = UploadedAudio("job-123.wav", b"audio content")
    manager = AudioManager(str(tmp_path))
    manager.save_audio(audio_file)

    deleted = manager.delete_audio(audio_file.filename)

    assert deleted is True
    assert (tmp_path / "job-123.wav").exists() is False


def test_delete_audio_returns_false_for_missing_file(tmp_path):
    missing_file = UploadedAudio("nonexistent.wav", b"")
    manager = AudioManager(str(tmp_path))

    deleted = manager.delete_audio(missing_file.filename)

    assert deleted is False

def test_save_audio_rejects_parent_directory_traversal(tmp_path):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))
    upload = UploadedAudio("../escaped.wav", b"audio content")

    with pytest.raises(WrongAudioPathError, match="Tentative de sauvegarde en dehors du dossier audio autorisé."):
        manager.save_audio(upload)

    assert (tmp_path / "escaped.wav").exists() is False

def test_delete_audio_rejects_file_outside_audio_directory(tmp_path):
    audio_directory = tmp_path / "audio"
    outside_file = UploadedAudio("../outside.wav", b"must be preserved")
    with open(str(tmp_path / outside_file.filename), "wb") as f:
        f.write(outside_file.content)  # Save outside the audio directory
    manager = AudioManager(str(audio_directory))

    with pytest.raises(WrongAudioPathError, match="Tentative de suppression en dehors du dossier audio autorisé."):
        manager.delete_audio(outside_file.filename)

    assert outside_file.content == b"must be preserved"

def test_delete_no_existing_audio_file(tmp_path):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))
    non_existing_file = UploadedAudio("nonexistent.wav", b"")

    result = manager.delete_audio(non_existing_file.filename)

    assert result is False

def test_open_audio_streams_saved_audio_and_closes_file(tmp_path):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))
    upload = UploadedAudio("job-123.wav", b"audio content")
    manager.save_audio(upload)

    with manager.open_audio("job-123.wav") as audio_stream:
        assert audio_stream.read() == b"audio content"
        assert audio_stream.closed is False

    assert audio_stream.closed is True

def test_open_audio_raises_for_missing_file(tmp_path):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))

    with pytest.raises(FileNotFoundError):
        with manager.open_audio("nonexistent.wav"):
            pass

def test_open_audio_rejects_file_outside_audio_directory(tmp_path):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))

    with pytest.raises(WrongAudioPathError):
        with manager.open_audio("../outside.wav"):
            pass
