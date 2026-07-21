from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeAlias

from sqlalchemy import Engine
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
) -> Iterator[Session]:
    with session_factory.begin() as session:
        yield session