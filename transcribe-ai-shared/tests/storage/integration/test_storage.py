import pytest

from transcribe_ai_shared.storage import (
    AudioStorageService,
    UploadedAudio,
    WrongAudioPathError,
)


pytestmark = pytest.mark.integration


def test_initialization_creates_audio_directory(tmp_path):
    audio_directory = tmp_path / "nested" / "audio"

    storage = AudioStorageService(audio_directory)

    assert audio_directory.is_dir()
    assert storage.audio_storage_path == audio_directory


def test_initialization_preserves_existing_directory_content(tmp_path):
    existing_file = tmp_path / "existing.wav"
    existing_file.write_bytes(b"existing audio")

    AudioStorageService(tmp_path)

    assert existing_file.read_bytes() == b"existing audio"


def test_save_audio_writes_file_and_returns_relative_filename(tmp_path):
    storage = AudioStorageService(tmp_path)
    upload = UploadedAudio("job-123.wav", b"audio content")

    saved_filename = storage.save_audio(upload)

    assert saved_filename == "job-123.wav"
    assert (tmp_path / saved_filename).read_bytes() == b"audio content"


def test_delete_audio_removes_existing_file(tmp_path):
    audio_file = UploadedAudio("job-123.wav", b"audio content")
    storage = AudioStorageService(tmp_path)
    storage.save_audio(audio_file)

    deleted = storage.delete_audio(audio_file.filename)

    assert deleted is True
    assert (tmp_path / "job-123.wav").exists() is False


def test_delete_audio_returns_false_for_missing_file(tmp_path):
    storage = AudioStorageService(tmp_path)

    deleted = storage.delete_audio("nonexistent.wav")

    assert deleted is False


@pytest.mark.parametrize("filename", ["../escaped.wav", "/absolute.wav"])
def test_save_audio_rejects_path_outside_audio_directory(tmp_path, filename):
    audio_directory = tmp_path / "audio"
    storage = AudioStorageService(audio_directory)
    upload = UploadedAudio(filename, b"audio content")

    with pytest.raises(WrongAudioPathError, match="sauvegarde"):
        storage.save_audio(upload)


@pytest.mark.parametrize("filename", ["../outside.wav", "/absolute.wav"])
def test_delete_audio_rejects_path_outside_audio_directory(tmp_path, filename):
    audio_directory = tmp_path / "audio"
    outside_file = tmp_path / "outside.wav"
    outside_file.write_bytes(b"must be preserved")
    storage = AudioStorageService(audio_directory)

    with pytest.raises(WrongAudioPathError, match="suppression"):
        storage.delete_audio(filename)

    assert outside_file.read_bytes() == b"must be preserved"


def test_open_audio_streams_saved_audio_and_closes_file(tmp_path):
    audio_directory = tmp_path / "audio"
    storage = AudioStorageService(audio_directory)
    storage.save_audio(UploadedAudio("job-123.wav", b"audio content"))

    with storage.open_audio("job-123.wav") as audio_stream:
        assert audio_stream.read() == b"audio content"
        assert audio_stream.closed is False

    assert audio_stream.closed is True


def test_open_audio_raises_for_missing_file(tmp_path):
    storage = AudioStorageService(tmp_path / "audio")

    with pytest.raises(FileNotFoundError):
        with storage.open_audio("nonexistent.wav"):
            pass


@pytest.mark.parametrize("filename", ["../outside.wav", "/absolute.wav"])
def test_open_audio_rejects_path_outside_audio_directory(tmp_path, filename):
    storage = AudioStorageService(tmp_path / "audio")

    with pytest.raises(WrongAudioPathError):
        with storage.open_audio(filename):
            pass
