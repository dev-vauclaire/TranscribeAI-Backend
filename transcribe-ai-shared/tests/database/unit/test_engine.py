from unittest.mock import Mock

from transcribe_ai_shared.database import engine as engine_module
from transcribe_ai_shared.database.config import DatabaseConfig


def test_create_db_engine_forwards_configuration(monkeypatch):
    expected_engine = Mock()
    create_engine = Mock(return_value=expected_engine)
    monkeypatch.setattr(engine_module, "create_engine", create_engine)
    config = DatabaseConfig(
        url="postgresql://user:secret@db/transcribe_test",
        echo=True,
        pool_size=5,
        max_overflow=2,
        pool_timeout_seconds=12.5,
    )

    result = engine_module.create_db_engine(config)

    assert result is expected_engine
    create_engine.assert_called_once_with(
        config.url,
        echo=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_timeout=12.5,
        hide_parameters=True,
    )
