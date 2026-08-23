import asyncio
import logging

from dispatcher.application import run_dispatch_cycle
from dispatcher.config import DispatcherSettings
from dispatcher.models import DispatcherCycleResult
from transcribe_ai_shared import DatabaseSettings, RedisSettings
from transcribe_ai_shared.observability import configure_logging, log_event


LOGGER = logging.getLogger(__name__)
SERVICE = "dispatcher"


async def _run_from_environment() -> DispatcherCycleResult:
    """Charge la configuration puis traite un cycle complet du dispatcher."""
    return await run_dispatch_cycle(
        database_settings=DatabaseSettings(),
        redis_settings=RedisSettings(),
        dispatcher_settings=DispatcherSettings(),
    )


def _log_summary(result: DispatcherCycleResult) -> None:
    log_event(
        LOGGER,
        logging.INFO,
        service=SERVICE,
        event="lease_recovery",
        action="summary",
        selected_count=result.recovery.selected_count,
        requeued_count=result.recovery.requeued_count,
        failed_count=result.recovery.failed_count,
        stale_count=result.recovery.stale_count,
        error_count=result.recovery.error_count,
    )
    log_event(
        LOGGER,
        logging.INFO,
        service=SERVICE,
        event="reconciliation",
        action="summary",
        selected_count=result.reconciliation.selected_count,
        rearmed_count=result.reconciliation.rearmed_count,
        stale_count=result.reconciliation.stale_count,
        error_count=result.reconciliation.error_count,
    )
    log_event(
        LOGGER,
        logging.INFO,
        service=SERVICE,
        event="dispatch",
        action="summary",
        selected_count=result.dispatch.selected_count,
        published_count=result.dispatch.published_count,
        confirmed_count=result.dispatch.confirmed_count,
        stale_count=result.dispatch.stale_count,
        error_count=result.dispatch.error_count,
    )


def main() -> int:
    """Traite un cycle et retourne un code exploitable par l'orchestrateur."""
    configure_logging(service=SERVICE)

    try:
        result = asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        log_event(
            LOGGER,
            logging.WARNING,
            service=SERVICE,
            event="dispatcher_cycle",
            action="interrupted",
        )
        return 130
    except Exception as error:
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE,
            event="dispatcher_cycle",
            action="failed",
            error_type=type(error).__name__,
        )
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
