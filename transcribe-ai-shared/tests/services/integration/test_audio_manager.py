import pytest

from transcribe_ai_shared.services import AudioManager, UploadedAudio
from transcribe_ai_shared.services.audio_manager import (
    WrongAudioPathError,
)


pytestmark = pytest.mark.integration


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


def test_save_audio_writes_file_and_returns_relative_filename(tmp_path):
    manager = AudioManager(str(tmp_path))
    upload = UploadedAudio("job-123.wav", b"audio content")

    saved_filename = manager.save_audio(upload)

    assert saved_filename == "job-123.wav"
    assert (tmp_path / saved_filename).read_bytes() == b"audio content"


def test_delete_audio_removes_existing_file(tmp_path):
    audio_file = UploadedAudio("job-123.wav", b"audio content")
    manager = AudioManager(str(tmp_path))
    manager.save_audio(audio_file)

    deleted = manager.delete_audio(audio_file.filename)

    assert deleted is True
    assert (tmp_path / "job-123.wav").exists() is False


def test_delete_audio_returns_false_for_missing_file(tmp_path):
    manager = AudioManager(str(tmp_path))

    deleted = manager.delete_audio("nonexistent.wav")

    assert deleted is False


@pytest.mark.parametrize("filename", ["../escaped.wav", "/absolute.wav"])
def test_save_audio_rejects_path_outside_audio_directory(tmp_path, filename):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))
    upload = UploadedAudio(filename, b"audio content")

    with pytest.raises(WrongAudioPathError, match="sauvegarde"):
        manager.save_audio(upload)


@pytest.mark.parametrize("filename", ["../outside.wav", "/absolute.wav"])
def test_delete_audio_rejects_path_outside_audio_directory(tmp_path, filename):
    audio_directory = tmp_path / "audio"
    outside_file = tmp_path / "outside.wav"
    outside_file.write_bytes(b"must be preserved")
    manager = AudioManager(str(audio_directory))

    with pytest.raises(WrongAudioPathError, match="suppression"):
        manager.delete_audio(filename)

    assert outside_file.read_bytes() == b"must be preserved"


def test_open_audio_streams_saved_audio_and_closes_file(tmp_path):
    audio_directory = tmp_path / "audio"
    manager = AudioManager(str(audio_directory))
    manager.save_audio(UploadedAudio("job-123.wav", b"audio content"))

    with manager.open_audio("job-123.wav") as audio_stream:
        assert audio_stream.read() == b"audio content"
        assert audio_stream.closed is False

    assert audio_stream.closed is True


def test_open_audio_raises_for_missing_file(tmp_path):
    manager = AudioManager(str(tmp_path / "audio"))

    with pytest.raises(FileNotFoundError):
        with manager.open_audio("nonexistent.wav"):
            pass


@pytest.mark.parametrize("filename", ["../outside.wav", "/absolute.wav"])
def test_open_audio_rejects_path_outside_audio_directory(tmp_path, filename):
    manager = AudioManager(str(tmp_path / "audio"))

    with pytest.raises(WrongAudioPathError):
        with manager.open_audio(filename):
            pass
