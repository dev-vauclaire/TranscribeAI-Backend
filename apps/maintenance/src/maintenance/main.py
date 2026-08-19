import asyncio
import logging

from transcribe_ai_shared import (
    DatabaseSettings,
    StorageSettings,
)

from maintenance.application import run_cleanup_storage
from maintenance.config import MaintenanceSettings
from maintenance.storage_cleanup import (
    StorageCleanupAbortedError,
    StorageCleanupResult,
)


LOGGER = logging.getLogger(__name__)


async def _run_from_environment() -> StorageCleanupResult:
    """Charge la configuration puis exécute l'unique tâche du processus."""
    return await run_cleanup_storage(
        database_settings=DatabaseSettings(),
        storage_settings=StorageSettings(),
        maintenance_settings=MaintenanceSettings(),
    )


def _log_summary(result: StorageCleanupResult) -> None:
    LOGGER.info(
        "storage_cleanup_summary inspected=%s kept=%s deleted=%s "
        "orphan_deleted=%s too_recent=%s errors=%s",
        result.inspected_count,
        result.kept_count,
        result.deleted_count,
        result.orphan_deleted_count,
        result.too_recent_count,
        result.error_count,
    )


def main() -> int:
    """Exécute le cleanup une fois et retourne un code exploitable par un cron."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        result = asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        LOGGER.warning("storage_cleanup_interrupted")
        return 130
    except StorageCleanupAbortedError:
        # Les causes internes peuvent encapsuler des paramètres de connexion.
        LOGGER.error("storage_cleanup_aborted")
        return 1
    except Exception as error:
        # Le type suffit au diagnostic initial sans risquer de journaliser un secret.
        LOGGER.error(
            "storage_cleanup_failed error_type=%s",
            type(error).__name__,
        )
        return 1

    _log_summary(result)
    return 1 if result.error_count else 0
