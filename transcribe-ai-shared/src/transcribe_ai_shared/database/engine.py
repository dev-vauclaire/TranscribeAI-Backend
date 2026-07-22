from sqlalchemy import Engine, create_engine

from transcribe_ai_shared.database.config import DatabaseConfig


# Return une instance Engine configurée
def create_db_engine(config: DatabaseConfig) -> Engine:
    return create_engine(
        config.url,
        echo=config.echo,
        pool_pre_ping=True,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_seconds,
        hide_parameters=True,
    )
