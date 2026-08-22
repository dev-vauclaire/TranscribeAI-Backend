from pydantic import ValidationError
import pytest

from dispatcher.config import DispatcherSettings


pytestmark = pytest.mark.unit


def test_batch_size_defaults_to_one_hundred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISPATCHER_BATCH_SIZE", raising=False)
    monkeypatch.delenv("MAX_ATTEMPTS", raising=False)

    settings = DispatcherSettings()

    assert settings.batch_size == 100
    assert settings.max_attempts == 3


def test_batch_size_can_be_configured_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCHER_BATCH_SIZE", "250")

    settings = DispatcherSettings()

    assert settings.batch_size == 250


def test_max_attempts_uses_the_shared_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    monkeypatch.setenv("DISPATCHER_MAX_ATTEMPTS", "7")

    settings = DispatcherSettings()

    assert settings.max_attempts == 5


@pytest.mark.parametrize("batch_size", [0, -1, 1_001])
def test_batch_size_must_remain_within_its_safe_bounds(batch_size: int) -> None:
    with pytest.raises(ValidationError):
        DispatcherSettings(batch_size=batch_size)


@pytest.mark.parametrize("max_attempts", [0, -1, 2_147_483_649])
def test_max_attempts_must_remain_within_attempt_count_bounds(
    max_attempts: int,
) -> None:
    with pytest.raises(ValidationError):
        DispatcherSettings(max_attempts=max_attempts)
