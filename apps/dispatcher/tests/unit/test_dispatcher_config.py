from pydantic import ValidationError
import pytest

from dispatcher.config import DispatcherSettings


pytestmark = pytest.mark.unit


def test_batch_size_defaults_to_one_hundred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISPATCHER_BATCH_SIZE", raising=False)

    settings = DispatcherSettings()

    assert settings.batch_size == 100


def test_batch_size_can_be_configured_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCHER_BATCH_SIZE", "250")

    settings = DispatcherSettings()

    assert settings.batch_size == 250


@pytest.mark.parametrize("batch_size", [0, -1, 1_001])
def test_batch_size_must_remain_within_its_safe_bounds(batch_size: int) -> None:
    with pytest.raises(ValidationError):
        DispatcherSettings(batch_size=batch_size)
