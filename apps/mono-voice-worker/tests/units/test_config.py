import pytest

from mono_voice_worker.config import WorkerMonoVoiceSettings


pytestmark = pytest.mark.unit

SETTING_ENV_NAMES = (
    "PG_DSN",
    "REDIS_DSN",
    "AUDIO_FOLDER_PATH",
    "WHISPER_SERVICE_URL",
    "REDIS_QUEUE_NAME_MONO_VOICE",
    "WORKER_LOOP_SLEEP_TIME",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    for environment_name in SETTING_ENV_NAMES:
        monkeypatch.delenv(environment_name, raising=False)


def test_settings_read_shared_and_worker_environment_variables(monkeypatch):
    monkeypatch.setenv(
        "PG_DSN",
        "postgresql+psycopg2://user:password@postgres.example/transcribe",
    )
    monkeypatch.setenv("REDIS_DSN", "redis://redis.example:6379/4")
    monkeypatch.setenv("AUDIO_FOLDER_PATH", "/var/lib/transcribe/audio")
    monkeypatch.setenv("WHISPER_SERVICE_URL", "http://whisper:5002")
    monkeypatch.setenv("REDIS_QUEUE_NAME_MONO_VOICE", "mono-tests")
    monkeypatch.setenv("WORKER_LOOP_SLEEP_TIME", "3")

    settings = WorkerMonoVoiceSettings()

    assert str(settings.pg_dsn).endswith("/transcribe")
    assert str(settings.redis_dsn) == "redis://redis.example:6379/4"
    assert settings.audio_folder_path == "/var/lib/transcribe/audio"
    assert settings.whisper_service_url == "http://whisper:5002"
    assert settings.redis_queue_name_mono_voice == "mono-tests"
    assert settings.worker_loop_sleep_time == 3


def test_settings_repr_hides_connection_and_service_values():
    settings = WorkerMonoVoiceSettings(
        pg_dsn="postgresql+psycopg2://user:password@postgres.example/transcribe",
        redis_dsn="redis://:password@redis.example:6379/4",
        audio_folder_path="/sensitive/audio/path",
        whisper_service_url="http://internal-whisper:5002",
        redis_queue_name_mono_voice="private-queue",
    )

    representation = repr(settings)

    assert "password" not in representation
    assert "/sensitive/audio/path" not in representation
    assert "internal-whisper" not in representation
    assert "private-queue" not in representation
