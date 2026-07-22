import pytest
from pydantic import PostgresDsn, RedisDsn, ValidationError

from transcribe_ai_shared import TranscribeAiBaseSettings


VALID_POSTGRES_DSN = "postgresql+psycopg2://user:password@postgres.example:5432/transcribe"
VALID_REDIS_DSN = "redis://:password@redis.example:6379/4"
SETTING_ENV_NAMES = (
    "PG_DSN",
    "REDIS_DSN",
    "AUDIO_FOLDER_PATH",
    "pg_dsn",
    "redis_dsn",
    "audio_folder_path",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    for environment_name in SETTING_ENV_NAMES:
        monkeypatch.delenv(environment_name, raising=False)


def test_settings_use_expected_non_database_defaults():
    settings = TranscribeAiBaseSettings(pg_dsn=VALID_POSTGRES_DSN)

    assert str(settings.redis_dsn) == "redis://localhost:6379/0"
    assert settings.audio_folder_path == "tmp/audios_buffers"

def test_default_settings_are_valid():
    settings = TranscribeAiBaseSettings()

    assert str(settings.pg_dsn).startswith("postgresql+")


def test_settings_read_uppercase_environment_variables(monkeypatch):
    monkeypatch.setenv("PG_DSN", VALID_POSTGRES_DSN)
    monkeypatch.setenv("REDIS_DSN", VALID_REDIS_DSN)
    monkeypatch.setenv("AUDIO_FOLDER_PATH", "/var/lib/transcribe/audio")

    settings = TranscribeAiBaseSettings()

    assert str(settings.pg_dsn) == VALID_POSTGRES_DSN
    assert str(settings.redis_dsn) == VALID_REDIS_DSN
    assert settings.audio_folder_path == "/var/lib/transcribe/audio"


def test_explicit_values_take_precedence_over_environment(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://environment:password@postgres.example/environment",)
    monkeypatch.setenv("REDIS_DSN", "redis://redis.example:6379/1")
    monkeypatch.setenv("AUDIO_FOLDER_PATH", "/environment/audio")

    settings = TranscribeAiBaseSettings(
        pg_dsn=VALID_POSTGRES_DSN,
        redis_dsn=VALID_REDIS_DSN,
        audio_folder_path="/explicit/audio",
    )

    assert str(settings.pg_dsn) == VALID_POSTGRES_DSN
    assert str(settings.redis_dsn) == VALID_REDIS_DSN
    assert settings.audio_folder_path == "/explicit/audio"


def test_settings_convert_dsn_strings_to_typed_urls():
    settings = TranscribeAiBaseSettings(
        pg_dsn=VALID_POSTGRES_DSN,
        redis_dsn=VALID_REDIS_DSN,
    )

    assert isinstance(settings.pg_dsn, PostgresDsn)
    assert isinstance(settings.redis_dsn, RedisDsn)


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "other_values"),
    [
        (
            "pg_dsn",
            "mysql://user:password@database/transcribe",
            {"redis_dsn": VALID_REDIS_DSN},
        ),
        (
            "redis_dsn",
            "https://redis.example:6379/0",
            {"pg_dsn": VALID_POSTGRES_DSN},
        ),
    ],
)
def test_settings_reject_invalid_dsn_schemes(
    field_name,
    invalid_value,
    other_values,
):
    with pytest.raises(ValidationError) as error:
        TranscribeAiBaseSettings(**other_values, **{field_name: invalid_value})

    assert error.value.errors()[0]["loc"] == (field_name,)
    assert error.value.errors()[0]["type"] == "url_scheme"

def test_settings_repr_does_not_expose_credentials():
    settings = TranscribeAiBaseSettings(
        pg_dsn=VALID_POSTGRES_DSN,
        redis_dsn=VALID_REDIS_DSN,
    )

    representation = repr(settings)

    assert "password" not in representation
