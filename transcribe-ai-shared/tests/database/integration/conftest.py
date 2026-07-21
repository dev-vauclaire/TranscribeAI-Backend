import os
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from transcribe_ai_shared.database.base import Base
from transcribe_ai_shared.database.config import DatabaseConfig
from transcribe_ai_shared.database.engine import create_db_engine
from transcribe_ai_shared.database.session import create_session_factory


TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def test_database_url():
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.fail(
            f"{TEST_DATABASE_URL_ENV} must contain the URL of a dedicated "
            "PostgreSQL test database"
        )

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        pytest.fail(f"{TEST_DATABASE_URL_ENV} must target PostgreSQL")

    return database_url


@pytest.fixture(scope="session")
def db_engine(test_database_url):
    schema = f"transcribe_ai_test_{uuid4().hex}"
    admin_engine = create_db_engine(DatabaseConfig(url=test_database_url))

    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    engine = create_db_engine(DatabaseConfig(url=test_database_url))

    @event.listens_for(engine, "connect", insert=True)
    def set_test_schema(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')

    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin_engine.dispose()


@pytest.fixture
def db_session(db_engine):
    factory = create_session_factory(db_engine)
    with factory() as session:
        yield session

    with db_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE jobs RESTART IDENTITY CASCADE"))
