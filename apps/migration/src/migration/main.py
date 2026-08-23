import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from transcribe_ai_shared.database import DatabaseSettings, create_db_engine
from transcribe_ai_shared.observability import configure_logging, log_event


ALEMBIC_CONFIG_PATH = Path(__file__).with_name("alembic.ini")
LOGGER = logging.getLogger(__name__)
SERVICE = "migration"


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
            # Le processus a déjà installé le formatter JSON commun.
            config.attributes["configure_logger"] = False
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def main() -> int:
    """Run all pending migrations once, then exit."""
    configure_logging(service=SERVICE)
    log_event(
        LOGGER,
        logging.INFO,
        service=SERVICE,
        event="migration",
        action="started",
    )
    try:
        upgrade_database()
    except KeyboardInterrupt:
        log_event(
            LOGGER,
            logging.WARNING,
            service=SERVICE,
            event="migration",
            action="interrupted",
        )
        return 130
    except Exception as error:
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE,
            event="migration",
            action="failed",
            dependency="postgresql",
            error_type=type(error).__name__,
        )
        return 1
    log_event(
        LOGGER,
        logging.INFO,
        service=SERVICE,
        event="migration",
        action="completed",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
