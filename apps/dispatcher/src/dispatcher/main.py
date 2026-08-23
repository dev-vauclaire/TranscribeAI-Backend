import asyncio
import logging

from dispatcher.application import run_dispatch_cycle
from dispatcher.config import DispatcherSettings
from dispatcher.models import DispatcherCycleResult
from transcribe_ai_shared import DatabaseSettings, RedisSettings


LOGGER = logging.getLogger(__name__)


async def _run_from_environment() -> DispatcherCycleResult:
    """Charge la configuration puis traite un cycle complet du dispatcher."""
    return await run_dispatch_cycle(
        database_settings=DatabaseSettings(),
        redis_settings=RedisSettings(),
        dispatcher_settings=DispatcherSettings(),
    )


def _log_summary(result: DispatcherCycleResult) -> None:
    LOGGER.info(
        "lease_recovery_summary selected=%s requeued=%s failed=%s stale=%s errors=%s",
        result.recovery.selected_count,
        result.recovery.requeued_count,
        result.recovery.failed_count,
        result.recovery.stale_count,
        result.recovery.error_count,
    )
    LOGGER.info(
        "dispatch_reconciliation_summary selected=%s rearmed=%s stale=%s errors=%s",
        result.reconciliation.selected_count,
        result.reconciliation.rearmed_count,
        result.reconciliation.stale_count,
        result.reconciliation.error_count,
    )
    LOGGER.info(
        "dispatch_batch_summary selected=%s published=%s confirmed=%s "
        "stale=%s errors=%s",
        result.dispatch.selected_count,
        result.dispatch.published_count,
        result.dispatch.confirmed_count,
        result.dispatch.stale_count,
        result.dispatch.error_count,
    )


def main() -> int:
    """Traite un cycle et retourne un code exploitable par l'orchestrateur."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        result = asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        LOGGER.warning("dispatcher_cycle_interrupted")
        return 130
    except Exception as error:
        # Le type permet le diagnostic initial sans journaliser de secret.
        LOGGER.error("dispatcher_cycle_failed error_type=%s", type(error).__name__)
        return 1

    _log_summary(result)
    if (
        result.recovery.error_count
        or result.reconciliation.error_count
        or result.dispatch.error_count
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
