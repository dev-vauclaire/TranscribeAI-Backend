from dataclasses import FrozenInstanceError

import pytest

from transcribe_ai_shared.database.config import DatabaseConfig


pytestmark = pytest.mark.unit


def test_database_config_has_conservative_pool_defaults():
    test_url = "postgresql://user:secret@db/transcribe_test"
    config = DatabaseConfig(url=test_url)

    assert config.echo is False
    assert config.pool_size == 3
    assert config.max_overflow == 0
    assert config.pool_timeout_seconds == 30.0
    assert config.url == test_url


def test_database_config_hides_credentials_from_repr():
    test_url = "postgresql://user:secret@db/transcribe_test"
    config = DatabaseConfig(url=test_url)

    assert "secret" not in repr(config)
    assert "postgresql" not in repr(config)


def test_database_config_is_immutable():
    config = DatabaseConfig(url="postgresql://user:secret@db/transcribe_test")

    with pytest.raises(FrozenInstanceError):
        config.echo = True
