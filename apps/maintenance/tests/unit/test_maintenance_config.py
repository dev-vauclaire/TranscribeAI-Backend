from datetime import timedelta

from pydantic import ValidationError
import pytest

from maintenance.config import MaintenanceSettings


pytestmark = pytest.mark.unit


def test_cleanup_grace_period_defaults_to_one_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAINTENANCE_CLEANUP_GRACE_PERIOD_SECONDS", raising=False)

    settings = MaintenanceSettings()

    assert settings.cleanup_grace_period_seconds == 3600
    assert settings.cleanup_grace_period == timedelta(hours=1)


def test_cleanup_grace_period_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAINTENANCE_CLEANUP_GRACE_PERIOD_SECONDS", "7200")

    settings = MaintenanceSettings()

    assert settings.cleanup_grace_period_seconds == 7200
    assert settings.cleanup_grace_period == timedelta(hours=2)


@pytest.mark.parametrize("value", [0, -1])
def test_cleanup_grace_period_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        MaintenanceSettings(cleanup_grace_period_seconds=value)
