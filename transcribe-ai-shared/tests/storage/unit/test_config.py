from pathlib import Path

import pytest
from pydantic import ValidationError

from transcribe_ai_shared.storage.config import StorageSettings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_storage_environment(monkeypatch):
    monkeypatch.delenv("AUDIO_STORAGE_PATH", raising=False)
    monkeypatch.delenv("audio_storage_path", raising=False)


def test_storage_settings_accept_valid_configuration():
    settings = StorageSettings(
        audio_storage_path="/var/lib/transcribe/audio",
        _env_file=None,
    )

    assert settings.audio_storage_path == Path("/var/lib/transcribe/audio")


@pytest.mark.parametrize("invalid_path", ["", "   "])
def test_storage_settings_reject_empty_path(invalid_path):
    with pytest.raises(ValidationError) as error:
        StorageSettings(audio_storage_path=invalid_path, _env_file=None)

    assert error.value.errors()[0]["loc"] == ("audio_storage_path",)
    assert error.value.errors()[0]["type"] == "value_error"


def test_storage_settings_use_default_path():
    settings = StorageSettings(_env_file=None)

    assert settings.audio_storage_path == Path("tmp/audios_buffers")


def test_storage_settings_read_environment(monkeypatch):
    monkeypatch.setenv("AUDIO_STORAGE_PATH", "/environment/audio")

    settings = StorageSettings(_env_file=None)

    assert settings.audio_storage_path == Path("/environment/audio")


def test_storage_settings_hide_path_from_repr():
    settings = StorageSettings(
        audio_storage_path="/var/lib/transcribe/audio",
        _env_file=None,
    )

    assert "/var/lib/transcribe/audio" not in repr(settings)
