import logging

from dispatcher.models import ReconciliationBatchResult
from dispatcher.protocols import DispatchReconciliationStore
from transcribe_ai_shared.observability import log_event


LOGGER = logging.getLogger(__name__)
SERVICE = "dispatcher"


class DispatchReconciliationService:
    """Réarme les publications trop anciennes sans interrompre le batch."""

    def __init__(self, *, job_store: DispatchReconciliationStore) -> None:
        self._job_store = job_store

    async def reconcile_batch(
        self,
        batch_size: int,
        reconciliation_timeout_seconds: int,
    ) -> ReconciliationBatchResult:
        """Réarme un batch selon le temps PostgreSQL et des gardes CAS."""
        self._validate_configuration(batch_size, reconciliation_timeout_seconds)
        jobs = await self._job_store.find_stale_dispatched_jobs(
            batch_size,
            reconciliation_timeout_seconds,
        )
        rearmed_count = 0
        stale_count = 0
        error_count = 0

        for job in jobs:
            try:
                rearmed = await self._job_store.rearm_stale_dispatch(
                    job,
                    reconciliation_timeout_seconds,
                )
            except Exception as error:
                error_count += 1
                log_event(
                    LOGGER,
                    logging.WARNING,
                    service=SERVICE,
                    event="reconciliation",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    action="failed",
                    dependency="postgresql",
                    error_type=type(error).__name__,
                )
                continue

            if rearmed:
                rearmed_count += 1
                log_event(
                    LOGGER,
                    logging.INFO,
                    service=SERVICE,
                    event="reconciliation",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    action="rearmed",
                )
            else:
                stale_count += 1
                log_event(
                    LOGGER,
                    logging.INFO,
                    service=SERVICE,
                    event="reconciliation",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    action="stale",
                )

        return ReconciliationBatchResult(
            selected_count=len(jobs),
            rearmed_count=rearmed_count,
            stale_count=stale_count,
            error_count=error_count,
        )

    @staticmethod
    def _validate_configuration(
        batch_size: int,
        reconciliation_timeout_seconds: int,
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size doit être un entier strictement positif")
        if (
            type(reconciliation_timeout_seconds) is not int
            or reconciliation_timeout_seconds <= 0
        ):
            raise ValueError(
                "reconciliation_timeout_seconds doit être un entier strictement positif"
            )
