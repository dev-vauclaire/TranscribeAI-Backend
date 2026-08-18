from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from transcribe_ai_shared.database.config import DatabaseSettings


def create_db_engine(config: DatabaseSettings) -> Engine:
    """Crée le moteur synchrone utilisé par Alembic et le code historique."""
    return create_engine(
        str(config.url),
        echo=config.echo,
        pool_pre_ping=True,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_seconds,
        hide_parameters=True,
    )


def create_async_db_engine(config: DatabaseSettings) -> AsyncEngine:
    """Crée le moteur asyncpg utilisé par les repositories asynchrones."""
    async_url = make_url(str(config.url)).set(drivername="postgresql+asyncpg")
    return create_async_engine(
        async_url,
        echo=config.echo,
        pool_pre_ping=True,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_seconds,
        hide_parameters=True,
    )
