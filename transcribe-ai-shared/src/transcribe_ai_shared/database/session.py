from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import TypeAlias

from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import Session, sessionmaker


SessionFactory: TypeAlias = sessionmaker[Session]
AsyncSessionFactory: TypeAlias = async_sessionmaker[AsyncSession]


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def create_async_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    """Crée des sessions async dont la transaction reste pilotée par l'appelant."""
    return async_sessionmaker(
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


@asynccontextmanager
async def async_transaction(
    session_factory: AsyncSessionFactory,
) -> AsyncGenerator[AsyncSession, None]:
    """Valide ou annule atomiquement une unité de travail asynchrone."""
    async with session_factory.begin() as session:
        yield session


def check_postgres_connection(session_factory: SessionFactory) -> None:
    with transaction(session_factory) as session:
        session.execute(text("SELECT 1"))
