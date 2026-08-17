from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from testcontainers.postgres import PostgresContainer

from transcribe_ai_shared.database.base import Base
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


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def setup_db(postgres_url):
    engine = create_db_engine(DatabaseSettings(url=postgres_url))
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
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
