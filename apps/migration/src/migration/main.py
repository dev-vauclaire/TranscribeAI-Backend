from pathlib import Path

from alembic import command
from alembic.config import Config

from transcribe_ai_shared.database import DatabaseSettings, create_db_engine


ALEMBIC_CONFIG_PATH = Path(__file__).with_name("alembic.ini")


def create_alembic_config() -> Config:
    """Build an Alembic configuration from the packaged configuration file."""
    return Config(str(ALEMBIC_CONFIG_PATH))


def upgrade_database(settings: DatabaseSettings | None = None) -> None:
    """Upgrade the configured PostgreSQL database to the latest revision."""
    database_settings = settings or DatabaseSettings()
    engine = create_db_engine(database_settings)
    try:
        with engine.begin() as connection:
            config = create_alembic_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def main() -> None:
    """Run all pending migrations once, then exit."""
    upgrade_database()
