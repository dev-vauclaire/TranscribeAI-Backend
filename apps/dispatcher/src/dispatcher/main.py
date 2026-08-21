import asyncio
import logging

from dispatcher.application import run_dispatch_batch
from dispatcher.config import DispatcherSettings
from dispatcher.models import DispatchBatchResult
from transcribe_ai_shared import DatabaseSettings, RedisSettings


LOGGER = logging.getLogger(__name__)


async def _run_from_environment() -> DispatchBatchResult:
    """Charge la configuration puis traite un unique batch de jobs."""
    return await run_dispatch_batch(
        database_settings=DatabaseSettings(),
        redis_settings=RedisSettings(),
        dispatcher_settings=DispatcherSettings(),
    )


def _log_summary(result: DispatchBatchResult) -> None:
    LOGGER.info(
        "dispatch_batch_summary selected=%s published=%s confirmed=%s "
        "stale=%s errors=%s",
        result.selected_count,
        result.published_count,
        result.confirmed_count,
        result.stale_count,
        result.error_count,
    )


def main() -> int:
    """Traite un batch et retourne un code exploitable par l'orchestrateur."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        result = asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        LOGGER.warning("dispatch_batch_interrupted")
        return 130
    except Exception as error:
        # Le type permet le diagnostic initial sans journaliser de secret.
        LOGGER.error("dispatch_batch_failed error_type=%s", type(error).__name__)
        return 1

    _log_summary(result)
    return 1 if result.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
