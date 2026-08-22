import pytest
from pydantic import ValidationError

from transcribe_ai_shared.worker import WorkerSettings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_worker_environment(monkeypatch):
    for environment_name in (
        "WORKER_ID",
        "WORKER_CONSUMER_GROUP",
        "WORKER_BLOCK_MILLISECONDS",
        "WORKER_LEASE_SECONDS",
        "WORKER_HEARTBEAT_SECONDS",
        "MAX_ATTEMPTS",
        "worker_id",
        "worker_consumer_group",
        "worker_block_milliseconds",
        "worker_lease_seconds",
        "worker_heartbeat_seconds",
        "max_attempts",
    ):
        monkeypatch.delenv(environment_name, raising=False)


def test_worker_settings_accept_valid_configuration():
    settings = WorkerSettings(
        worker_id="worker-fast-1",
        worker_lease_seconds=120,
        worker_heartbeat_seconds=30,
        max_attempts=5,
        _env_file=None,
    )

    assert settings.worker_id == "worker-fast-1"
    assert settings.worker_consumer_group == "transcription-workers"
    assert settings.worker_block_milliseconds == 5_000
    assert settings.worker_lease_seconds == 120
    assert settings.worker_heartbeat_seconds == 30
    assert settings.max_attempts == 5


def test_worker_settings_require_worker_id():
    with pytest.raises(ValidationError) as error:
        WorkerSettings(_env_file=None)

    assert error.value.errors()[0]["loc"] == ("worker_id",)
    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "error_type"),
    [
        ("worker_id", "   ", "string_too_short"),
        ("worker_id", "w" * 256, "string_too_long"),
        ("worker_consumer_group", "   ", "string_too_short"),
        ("worker_block_milliseconds", 0, "greater_than"),
        ("worker_block_milliseconds", 10_000, "less_than"),
        ("worker_lease_seconds", 0, "greater_than"),
        ("worker_heartbeat_seconds", 0, "greater_than"),
        ("max_attempts", 0, "greater_than_equal"),
        ("max_attempts", 2_147_483_649, "less_than_equal"),
    ],
)
def test_worker_settings_reject_invalid_values(
    field_name,
    invalid_value,
    error_type,
):
    values = {"worker_id": "worker-fast-1", field_name: invalid_value}

    with pytest.raises(ValidationError) as error:
        WorkerSettings(_env_file=None, **values)

    assert error.value.errors()[0]["loc"] == (field_name,)
    assert error.value.errors()[0]["type"] == error_type


@pytest.mark.parametrize(
    ("lease_seconds", "heartbeat_seconds"),
    [(60, 60), (60, 61)],
)
def test_worker_settings_require_heartbeat_before_lease_expiry(
    lease_seconds: int,
    heartbeat_seconds: int,
) -> None:
    with pytest.raises(ValidationError, match="WORKER_HEARTBEAT_SECONDS"):
        WorkerSettings(
            worker_id="worker-fast-1",
            worker_lease_seconds=lease_seconds,
            worker_heartbeat_seconds=heartbeat_seconds,
            _env_file=None,
        )


def test_worker_settings_use_defaults():
    settings = WorkerSettings(worker_id="worker-fast-1", _env_file=None)

    assert settings.worker_lease_seconds == 300
    assert settings.worker_heartbeat_seconds == 60
    assert settings.max_attempts == 3
    assert settings.worker_consumer_group == "transcription-workers"
    assert settings.worker_block_milliseconds == 5_000


def test_worker_settings_read_environment(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "worker-from-environment")
    monkeypatch.setenv("WORKER_CONSUMER_GROUP", "custom-workers")
    monkeypatch.setenv("WORKER_BLOCK_MILLISECONDS", "750")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "600")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "120")
    monkeypatch.setenv("MAX_ATTEMPTS", "7")

    settings = WorkerSettings(_env_file=None)

    assert settings.worker_id == "worker-from-environment"
    assert settings.worker_consumer_group == "custom-workers"
    assert settings.worker_block_milliseconds == 750
    assert settings.worker_lease_seconds == 600
    assert settings.worker_heartbeat_seconds == 120
    assert settings.max_attempts == 7


def test_worker_settings_reject_unknown_field():
    with pytest.raises(ValidationError) as error:
        WorkerSettings(
            worker_id="worker-fast-1",
            max_attemps=5,
            _env_file=None,
        )

    assert error.value.errors()[0]["loc"] == ("max_attemps",)
    assert error.value.errors()[0]["type"] == "extra_forbidden"
