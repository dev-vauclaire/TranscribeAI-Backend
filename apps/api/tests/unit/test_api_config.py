"""Tests de la configuration propre à l'application API."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from api.config import (
    ApiSettings,
    DEFAULT_BATCH_MAX_DURATION_SECONDS,
    DEFAULT_FAST_MAX_DURATION_SECONDS,
    DEFAULT_MAX_UPLOAD_SIZE_BYTES,
)


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_api_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "API_HOST",
        "API_PORT",
        "API_MAX_UPLOAD_SIZE_BYTES",
        "FFPROBE_PATH",
        "FFPROBE_TIMEOUT_SECONDS",
        "API_FAST_MAX_DURATION_SECONDS",
        "API_BATCH_MAX_DURATION_SECONDS",
    ):
        monkeypatch.delenv(variable, raising=False)
        monkeypatch.delenv(variable.lower(), raising=False)


def test_api_settings_default_upload_limit_is_100_mib() -> None:
    settings = ApiSettings()

    assert DEFAULT_MAX_UPLOAD_SIZE_BYTES == 100 * 1024 * 1024
    assert settings.max_upload_size_bytes == DEFAULT_MAX_UPLOAD_SIZE_BYTES


def test_api_settings_use_documented_duration_defaults() -> None:
    settings = ApiSettings()

    assert DEFAULT_FAST_MAX_DURATION_SECONDS == Decimal("900")
    assert DEFAULT_BATCH_MAX_DURATION_SECONDS == Decimal("14400")
    assert settings.fast_max_duration_seconds == Decimal("900")
    assert settings.batch_max_duration_seconds == Decimal("14400")


def test_api_settings_override_upload_limit_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_MAX_UPLOAD_SIZE_BYTES", "52428800")

    settings = ApiSettings()

    assert settings.max_upload_size_bytes == 50 * 1024 * 1024


def test_api_settings_reject_non_positive_upload_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_MAX_UPLOAD_SIZE_BYTES", "0")

    with pytest.raises(ValidationError):
        ApiSettings()


def test_api_settings_override_duration_limits_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_FAST_MAX_DURATION_SECONDS", "120.5")
    monkeypatch.setenv("API_BATCH_MAX_DURATION_SECONDS", "7200")

    settings = ApiSettings()

    assert settings.fast_max_duration_seconds == Decimal("120.5")
    assert settings.batch_max_duration_seconds == Decimal("7200")


def test_api_settings_reject_fast_limit_above_batch_limit() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(
            fast_max_duration_seconds=Decimal("61"),
            batch_max_duration_seconds=Decimal("60"),
        )


@pytest.mark.parametrize("duration", [Decimal("0"), Decimal("-1")])
def test_api_settings_reject_non_positive_duration_limits(
    duration: Decimal,
) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(fast_max_duration_seconds=duration)


def test_api_settings_reject_blank_ffprobe_path() -> None:
    with pytest.raises(ValidationError):
        ApiSettings(ffprobe_path="   ")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_api_settings_reject_invalid_ffprobe_timeout(timeout: float) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(ffprobe_timeout_seconds=timeout)
