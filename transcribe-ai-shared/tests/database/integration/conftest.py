from collections.abc import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, MetaData, delete

from testcontainers.postgres import PostgresContainer

from migration.main import create_alembic_config
from transcribe_ai_shared.database.config import DatabaseSettings
from transcribe_ai_shared.database.engine import create_db_engine
from transcribe_ai_shared.database.models import (
    OutboxEvent,
    TranscriptionJob,
    TranscriptionResult,
)
from transcribe_ai_shared.database.session import (
    SessionFactory,
    create_session_factory,
)


def run_alembic_command(
    engine: Engine,
    config: Config,
    operation: Callable[[Config, str], None],
    target: str,
) -> None:
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        try:
            operation(config, target)
        finally:
            config.attributes.pop("connection", None)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def forbid_metadata_create_all() -> Iterator[None]:
    original_create_all = MetaData.create_all

    def forbidden_create_all(*args, **kwargs) -> None:
        raise AssertionError("Database integration tests must use Alembic")

    MetaData.create_all = forbidden_create_all
    try:
        yield
    finally:
        MetaData.create_all = original_create_all


@pytest.fixture(scope="session")
def setup_db(postgres_url, forbid_metadata_create_all):
    engine = create_db_engine(DatabaseSettings(url=postgres_url))
    config = create_alembic_config()
    upgraded = False
    try:
        run_alembic_command(engine, config, command.upgrade, "head")
        upgraded = True
        yield engine
    finally:
        if upgraded:
            run_alembic_command(engine, config, command.downgrade, "base")
        engine.dispose()


@pytest.fixture(scope="session")
def session_factory(setup_db):
    return create_session_factory(setup_db)


@pytest.fixture
def db_session(session_factory):
    with session_factory() as session:
        yield session
        session.rollback()


@pytest.fixture(autouse=True)
def clean_database(session_factory: SessionFactory) -> Iterator[None]:
    yield

    with session_factory.begin() as session:
        session.execute(delete(TranscriptionResult))
        session.execute(delete(OutboxEvent))
        session.execute(delete(TranscriptionJob))
