from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import Engine, MetaData
from testcontainers.postgres import PostgresContainer

from migration.main import create_alembic_config
from transcribe_ai_shared.database import DatabaseSettings, create_db_engine


@pytest.fixture(autouse=True)
def forbid_metadata_create_all(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_create_all(*args, **kwargs) -> None:
        raise AssertionError("Alembic integration tests must not call create_all()")

    monkeypatch.setattr(MetaData, "create_all", forbidden_create_all)


@pytest.fixture
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
def database_settings(postgres_container: PostgresContainer) -> DatabaseSettings:
    return DatabaseSettings(url=postgres_container.get_connection_url())


@pytest.fixture
def database_engine(database_settings: DatabaseSettings) -> Iterator[Engine]:
    engine = create_db_engine(database_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def alembic_config() -> Config:
    return create_alembic_config()
