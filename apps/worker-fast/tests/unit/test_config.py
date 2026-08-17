import pytest

from worker_fast.config import WorkerMonoVoiceSettings


pytestmark = pytest.mark.unit

SETTING_ENV_NAMES = (
    "WORKER_ID",
    "WORKER_LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "WHISPER_SERVICE_URL",
    "REDIS_QUEUE_NAME_MONO_VOICE",
    "WORKER_LOOP_SLEEP_TIME",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    for environment_name in SETTING_ENV_NAMES:
        monkeypatch.delenv(environment_name, raising=False)


def test_settings_read_worker_environment_variables(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "worker-fast-test")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("WHISPER_SERVICE_URL", "http://whisper:5002")
    monkeypatch.setenv("REDIS_QUEUE_NAME_MONO_VOICE", "mono-tests")
    monkeypatch.setenv("WORKER_LOOP_SLEEP_TIME", "3")

    settings = WorkerMonoVoiceSettings()

    assert settings.worker_id == "worker-fast-test"
    assert settings.worker_lease_seconds == 120
    assert settings.max_attempts == 5
    assert settings.whisper_service_url == "http://whisper:5002"
    assert settings.redis_queue_name_mono_voice == "mono-tests"
    assert settings.worker_loop_sleep_time == 3


def test_settings_repr_hides_connection_and_service_values():
    settings = WorkerMonoVoiceSettings(
        worker_id="worker-fast-test",
        whisper_service_url="http://internal-whisper:5002",
        redis_queue_name_mono_voice="private-queue",
    )

    representation = repr(settings)

    assert "internal-whisper" not in representation
    assert "private-queue" not in representation
