import pytest
from pydantic import RedisDsn, ValidationError

from transcribe_ai_shared.queue.config import RedisSettings


VALID_REDIS_URL = "redis://:password@redis.example:6379/4"

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_redis_environment(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("redis_url", raising=False)


def test_redis_settings_accept_valid_configuration():
    settings = RedisSettings(redis_url=VALID_REDIS_URL, _env_file=None)

    assert isinstance(settings.redis_url, RedisDsn)
    assert str(settings.redis_url) == VALID_REDIS_URL


def test_redis_settings_require_url():
    with pytest.raises(ValidationError) as error:
        RedisSettings(_env_file=None)

    assert error.value.errors()[0]["loc"] == ("redis_url",)
    assert error.value.errors()[0]["type"] == "missing"


def test_redis_settings_reject_invalid_url():
    with pytest.raises(ValidationError) as error:
        RedisSettings(redis_url="https://redis.example:6379/0", _env_file=None)

    assert error.value.errors()[0]["loc"] == ("redis_url",)
    assert error.value.errors()[0]["type"] == "url_scheme"


def test_redis_settings_read_environment(monkeypatch):
    monkeypatch.setenv("REDIS_URL", VALID_REDIS_URL)

    settings = RedisSettings(_env_file=None)

    assert str(settings.redis_url) == VALID_REDIS_URL


def test_redis_settings_hide_credentials_from_repr():
    settings = RedisSettings(redis_url=VALID_REDIS_URL, _env_file=None)

    assert "password" not in repr(settings)


def test_redis_settings_hide_invalid_credentials_from_error():
    secret = "redis-secret"

    with pytest.raises(ValidationError) as error:
        RedisSettings(
            redis_url=f"https://user:{secret}@redis.example:6379/0",
            _env_file=None,
        )

    assert secret not in str(error.value)
