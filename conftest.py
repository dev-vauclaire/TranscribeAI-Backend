from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

from migration.main import create_alembic_config
from transcribe_ai_shared.database.config import DatabaseSettings
from transcribe_ai_shared.database.engine import (
    create_async_db_engine,
    create_db_engine,
)
from transcribe_ai_shared.database.session import (
    SessionFactory,
    create_async_session_factory,
    create_session_factory,
)


def run_alembic_command(
    engine: Engine,
    config: Config,
    operation: Callable[[Config, str], None],
    target: str,
) -> None:
    """Exécute Alembic sur la connexion PostgreSQL fournie par Testcontainers."""
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
    """Garantit que les intégrations communes construisent le schéma via Alembic."""
    original_create_all = MetaData.create_all

    def forbidden_create_all(*args, **kwargs) -> None:
        raise AssertionError("Database integration tests must use Alembic")

    MetaData.create_all = forbidden_create_all
    try:
        yield
    finally:
        MetaData.create_all = original_create_all


@pytest.fixture(scope="session")
def setup_db(
    postgres_url: str,
    forbid_metadata_create_all: None,
) -> Iterator[Engine]:
    """Applique puis retire les migrations autour des intégrations applicatives."""
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
def session_factory(setup_db: Engine) -> SessionFactory:
    return create_session_factory(setup_db)


@pytest_asyncio.fixture
async def async_session_factory(
    postgres_container: PostgresContainer,
    setup_db: Engine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Fournit des sessions async sur le schéma créé par Alembic."""
    settings = DatabaseSettings(url=postgres_container.get_connection_url())
    engine = create_async_db_engine(settings)
    factory = create_async_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()
