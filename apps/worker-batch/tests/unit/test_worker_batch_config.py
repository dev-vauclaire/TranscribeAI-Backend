from pydantic import ValidationError
import pytest

from worker_batch.config import WorkerBatchSettings


pytestmark = pytest.mark.unit

SETTING_ENV_NAMES = (
    "WORKER_ID",
    "WORKER_CONSUMER_GROUP",
    "WORKER_BLOCK_MILLISECONDS",
    "WORKER_LEASE_SECONDS",
    "WORKER_HEARTBEAT_SECONDS",
    "MAX_ATTEMPTS",
    "WORKER_ENVIRONMENT",
    "WORKER_TRANSCRIBER_BACKEND",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for environment_name in SETTING_ENV_NAMES:
        monkeypatch.delenv(environment_name, raising=False)


def test_settings_default_to_production_without_a_transcriber_backend() -> None:
    settings = WorkerBatchSettings(worker_id="batch-1")

    assert settings.worker_environment == "production"
    assert settings.worker_transcriber_backend is None
    assert settings.worker_consumer_group == "transcription-workers"
    assert settings.worker_block_milliseconds == 5_000
    assert settings.worker_heartbeat_seconds == 60


def test_settings_read_worker_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_ID", "batch-env")
    monkeypatch.setenv("WORKER_CONSUMER_GROUP", "batch-workers")
    monkeypatch.setenv("WORKER_BLOCK_MILLISECONDS", "250")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("WORKER_ENVIRONMENT", "development")
    monkeypatch.setenv("WORKER_TRANSCRIBER_BACKEND", "fake")

    settings = WorkerBatchSettings()

    assert settings.worker_id == "batch-env"
    assert settings.worker_consumer_group == "batch-workers"
    assert settings.worker_block_milliseconds == 250
    assert settings.worker_lease_seconds == 120
    assert settings.worker_heartbeat_seconds == 30
    assert settings.max_attempts == 5
    assert settings.worker_environment == "development"
    assert settings.worker_transcriber_backend == "fake"


def test_fake_transcriber_is_rejected_outside_development() -> None:
    with pytest.raises(ValidationError, match="réservé"):
        WorkerBatchSettings(
            worker_id="batch-1",
            worker_environment="production",
            worker_transcriber_backend="fake",
        )
