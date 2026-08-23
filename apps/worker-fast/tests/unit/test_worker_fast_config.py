from pydantic import ValidationError
import pytest

from worker_fast.config import WorkerFastSettings


pytestmark = pytest.mark.unit

SETTING_ENV_NAMES = (
    "WORKER_ID",
    "WORKER_CONSUMER_GROUP",
    "WORKER_BLOCK_MILLISECONDS",
    "WORKER_AUTOCLAIM_INTERVAL_SECONDS",
    "WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS",
    "WORKER_LEASE_SECONDS",
    "WORKER_HEARTBEAT_SECONDS",
    "MAX_ATTEMPTS",
    "WORKER_ENVIRONMENT",
    "WORKER_TRANSCRIBER_BACKEND",
    "WORKER_TRANSCRIBER_MODEL",
    "WORKER_TRANSCRIBER_DEVICE",
    "WORKER_TRANSCRIBER_COMPUTE_TYPE",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for environment_name in SETTING_ENV_NAMES:
        monkeypatch.delenv(environment_name, raising=False)


def test_settings_default_to_the_low_latency_faster_whisper_backend() -> None:
    settings = WorkerFastSettings(worker_id="fast-1")

    assert settings.worker_environment == "production"
    assert settings.worker_transcriber_backend == "faster-whisper"
    assert settings.worker_transcriber_model == "large-v3-turbo"
    assert settings.worker_transcriber_device == "cuda"
    assert settings.worker_transcriber_compute_type == "float16"
    assert settings.worker_consumer_group == "transcription-workers"
    assert settings.worker_block_milliseconds == 5_000
    assert settings.worker_autoclaim_interval_seconds == 60
    assert settings.worker_autoclaim_min_idle_milliseconds == 300_000
    assert settings.worker_heartbeat_seconds == 60


def test_settings_read_worker_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_ID", "fast-env")
    monkeypatch.setenv("WORKER_CONSUMER_GROUP", "fast-workers")
    monkeypatch.setenv("WORKER_BLOCK_MILLISECONDS", "250")
    monkeypatch.setenv("WORKER_AUTOCLAIM_INTERVAL_SECONDS", "20")
    monkeypatch.setenv("WORKER_AUTOCLAIM_MIN_IDLE_MILLISECONDS", "60000")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("WORKER_ENVIRONMENT", "production")
    monkeypatch.setenv("WORKER_TRANSCRIBER_BACKEND", "faster-whisper")
    monkeypatch.setenv("WORKER_TRANSCRIBER_MODEL", "small")
    monkeypatch.setenv("WORKER_TRANSCRIBER_DEVICE", "cpu")
    monkeypatch.setenv("WORKER_TRANSCRIBER_COMPUTE_TYPE", "int8")

    settings = WorkerFastSettings()

    assert settings.worker_id == "fast-env"
    assert settings.worker_consumer_group == "fast-workers"
    assert settings.worker_block_milliseconds == 250
    assert settings.worker_autoclaim_interval_seconds == 20
    assert settings.worker_autoclaim_min_idle_milliseconds == 60_000
    assert settings.worker_lease_seconds == 120
    assert settings.worker_heartbeat_seconds == 30
    assert settings.max_attempts == 5
    assert settings.worker_environment == "production"
    assert settings.worker_transcriber_backend == "faster-whisper"
    assert settings.worker_transcriber_model == "small"
    assert settings.worker_transcriber_device == "cpu"
    assert settings.worker_transcriber_compute_type == "int8"


def test_fake_transcriber_is_rejected_outside_development() -> None:
    with pytest.raises(ValidationError, match="réservé"):
        WorkerFastSettings(
            worker_id="fast-1",
            worker_environment="production",
            worker_transcriber_backend="fake",
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("worker_transcriber_device", "tpu"),
        ("worker_transcriber_compute_type", "float64"),
    ],
)
def test_settings_reject_unsupported_engine_options(
    field_name: str,
    invalid_value: str,
) -> None:
    with pytest.raises(ValidationError):
        WorkerFastSettings(
            worker_id="fast-1",
            **{field_name: invalid_value},
        )


def test_settings_reject_an_empty_model_name() -> None:
    with pytest.raises(ValidationError):
        WorkerFastSettings(
            worker_id="fast-1",
            worker_transcriber_model="   ",
        )
