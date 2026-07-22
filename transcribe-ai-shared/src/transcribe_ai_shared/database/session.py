from contextlib import contextmanager
from typing import Generator, TypeAlias

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker


SessionFactory: TypeAlias = sessionmaker[Session]


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def transaction(
    session_factory: SessionFactory,
) -> Generator[Session, None, None]:
    with session_factory.begin() as session:
        yield session


def check_postgres_connection(session_factory: SessionFactory) -> None:
    with transaction(session_factory) as session:
        session.execute(text("SELECT 1"))
