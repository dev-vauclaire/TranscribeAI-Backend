import pytest
from pydantic import PostgresDsn, ValidationError

from transcribe_ai_shared.database.config import DatabaseSettings


VALID_DATABASE_URL = (
    "postgresql+psycopg2://user:password@postgres.example:5432/transcribe"
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_database_environment(monkeypatch):
    for field_name in (
        "URL",
        "ECHO",
        "POOL_SIZE",
        "MAX_OVERFLOW",
        "POOL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(f"DATABASE_{field_name}", raising=False)
        monkeypatch.delenv(f"database_{field_name.lower()}", raising=False)


def test_database_settings_accept_valid_configuration():
    settings = DatabaseSettings(url=VALID_DATABASE_URL, _env_file=None)

    assert isinstance(settings.url, PostgresDsn)
    assert str(settings.url) == VALID_DATABASE_URL


def test_database_settings_require_url():
    with pytest.raises(ValidationError) as error:
        DatabaseSettings(_env_file=None)

    assert error.value.errors()[0]["loc"] == ("url",)
    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "error_type"),
    [
        ("url", "mysql://user:password@database/transcribe", "url_scheme"),
        (
            "url",
            "postgresql+asyncpg://user:password@database/transcribe",
            "value_error",
        ),
        ("pool_size", 0, "greater_than"),
        ("max_overflow", -1, "greater_than_equal"),
        ("pool_timeout_seconds", 0, "greater_than"),
    ],
)
def test_database_settings_reject_invalid_values(
    field_name,
    invalid_value,
    error_type,
):
    values = {"url": VALID_DATABASE_URL, field_name: invalid_value}

    with pytest.raises(ValidationError) as error:
        DatabaseSettings(_env_file=None, **values)

    assert error.value.errors()[0]["loc"] == (field_name,)
    assert error.value.errors()[0]["type"] == error_type


def test_database_settings_use_pool_defaults():
    settings = DatabaseSettings(url=VALID_DATABASE_URL, _env_file=None)

    assert settings.echo is False
    assert settings.pool_size == 4
    assert settings.max_overflow == 0
    assert settings.pool_timeout_seconds == 30.0


def test_database_settings_read_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", VALID_DATABASE_URL)
    monkeypatch.setenv("DATABASE_POOL_SIZE", "8")

    settings = DatabaseSettings(_env_file=None)

    assert str(settings.url) == VALID_DATABASE_URL
    assert settings.pool_size == 8


def test_database_settings_hide_credentials_from_repr():
    settings = DatabaseSettings(url=VALID_DATABASE_URL, _env_file=None)

    assert "password" not in repr(settings)


def test_database_settings_hide_invalid_credentials_from_error():
    secret = "database-secret"

    with pytest.raises(ValidationError) as error:
        DatabaseSettings(
            url=f"mysql://user:{secret}@database/transcribe",
            _env_file=None,
        )

    assert secret not in str(error.value)


def test_database_settings_are_immutable():
    settings = DatabaseSettings(url=VALID_DATABASE_URL, _env_file=None)

    with pytest.raises(ValidationError) as error:
        settings.echo = True

    assert error.value.errors()[0]["type"] == "frozen_instance"
