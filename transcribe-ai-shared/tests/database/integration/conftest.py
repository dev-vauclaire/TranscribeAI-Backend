from collections.abc import Iterator

import pytest
from sqlalchemy import delete

from testcontainers.postgres import PostgresContainer

from transcribe_ai_shared.database.base import Base
from transcribe_ai_shared.database.config import DatabaseConfig
from transcribe_ai_shared.database.engine import create_db_engine
from transcribe_ai_shared.database.models.job_model import Job
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
def setup_db(request, postgres_url):

    def cleanup():
        Base.metadata.drop_all(engine)
        engine.dispose()

    request.addfinalizer(cleanup)
    engine = create_db_engine(DatabaseConfig(url=postgres_url))
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture(scope="session")
def session_factory(setup_db):
    return create_session_factory(setup_db)


@pytest.fixture
def db_session(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def clean_jobs_table(session_factory: SessionFactory) -> Iterator[None]:
    yield

    with session_factory.begin() as session:
        session.execute(delete(Job))
