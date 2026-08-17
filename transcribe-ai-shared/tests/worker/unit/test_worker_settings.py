import pytest
from pydantic import ValidationError

from transcribe_ai_shared.worker import WorkerSettings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_worker_environment(monkeypatch):
    for environment_name in (
        "WORKER_ID",
        "WORKER_LEASE_SECONDS",
        "MAX_ATTEMPTS",
        "worker_id",
        "worker_lease_seconds",
        "max_attempts",
    ):
        monkeypatch.delenv(environment_name, raising=False)


def test_worker_settings_accept_valid_configuration():
    settings = WorkerSettings(
        worker_id="worker-fast-1",
        worker_lease_seconds=120,
        max_attempts=5,
        _env_file=None,
    )

    assert settings.worker_id == "worker-fast-1"
    assert settings.worker_lease_seconds == 120
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
        ("worker_lease_seconds", 0, "greater_than"),
        ("max_attempts", 0, "greater_than_equal"),
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


def test_worker_settings_use_defaults():
    settings = WorkerSettings(worker_id="worker-fast-1", _env_file=None)

    assert settings.worker_lease_seconds == 300
    assert settings.max_attempts == 3


def test_worker_settings_read_environment(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "worker-from-environment")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "600")
    monkeypatch.setenv("MAX_ATTEMPTS", "7")

    settings = WorkerSettings(_env_file=None)

    assert settings.worker_id == "worker-from-environment"
    assert settings.worker_lease_seconds == 600
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
