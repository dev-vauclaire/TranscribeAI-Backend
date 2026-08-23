import asyncio
import logging

from transcribe_ai_shared import (
    DatabaseSettings,
    StorageSettings,
)
from transcribe_ai_shared.observability import configure_logging, log_event

from maintenance.application import run_cleanup_storage
from maintenance.config import MaintenanceSettings
from maintenance.storage_cleanup import (
    StorageCleanupAbortedError,
    StorageCleanupResult,
)


LOGGER = logging.getLogger(__name__)
SERVICE_NAME = "maintenance"


async def _run_from_environment() -> StorageCleanupResult:
    """Charge la configuration puis exécute l'unique tâche du processus."""
    return await run_cleanup_storage(
        database_settings=DatabaseSettings(),
        storage_settings=StorageSettings(),
        maintenance_settings=MaintenanceSettings(),
    )


def _log_summary(result: StorageCleanupResult) -> None:
    log_event(
        LOGGER,
        logging.INFO,
        service=SERVICE_NAME,
        event="cleanup",
        action="summary",
        inspected_count=result.inspected_count,
        kept_count=result.kept_count,
        deleted_count=result.deleted_count,
        orphan_deleted_count=result.orphan_deleted_count,
        too_recent_count=result.too_recent_count,
        error_count=result.error_count,
    )


def main() -> int:
    """Exécute le cleanup une fois et retourne un code exploitable par un cron."""
    configure_logging(service=SERVICE_NAME)

    try:
        result = asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        log_event(
            LOGGER,
            logging.WARNING,
            service=SERVICE_NAME,
            event="cleanup",
            action="interrupted",
            reason="keyboard_interrupt",
        )
        return 130
    except StorageCleanupAbortedError as error:
        # Les causes internes peuvent encapsuler des paramètres de connexion.
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE_NAME,
            event="cleanup",
            action="abort",
            dependency=error.dependency,
            reason=error.reason,
            error_type=error.error_type,
        )
        return 1
    except Exception as error:
        # Le type suffit au diagnostic initial sans risquer de journaliser un secret.
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE_NAME,
            event="cleanup",
            action="failed",
            reason="unexpected_error",
            error_type=type(error).__name__,
        )
        return 1

    _log_summary(result)
    return 1 if result.error_count else 0
