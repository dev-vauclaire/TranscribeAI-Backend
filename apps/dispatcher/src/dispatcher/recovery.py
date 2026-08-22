import logging

from dispatcher.models import RecoveryBatchResult
from dispatcher.protocols import ExpiredJobStore
from transcribe_ai_shared.retry_policy import (
    can_schedule_next_attempt,
    validate_max_attempts,
)


LOGGER = logging.getLogger(__name__)


class LeaseRecoveryService:
    """Récupère un batch de jobs dont le lease PostgreSQL a expiré."""

    def __init__(
        self,
        *,
        job_store: ExpiredJobStore,
    ) -> None:
        self._job_store = job_store

    async def recover_batch(
        self,
        batch_size: int,
        max_attempts: int,
    ) -> RecoveryBatchResult:
        """Récupère un batch sans interrompre les jobs suivants sur erreur."""
        self._validate_configuration(batch_size, max_attempts)
        jobs = await self._job_store.find_expired_jobs(batch_size)
        requeued_count = 0
        failed_count = 0
        stale_count = 0
        error_count = 0

        for job in jobs:
            try:
                should_retry = can_schedule_next_attempt(
                    attempt_count=job.attempt_count,
                    max_attempts=max_attempts,
                )
                recovered = await self._job_store.recover_expired_job(
                    job,
                    should_retry=should_retry,
                )
            except Exception as error:
                error_count += 1
                LOGGER.warning(
                    "dispatcher_recovery_error job_uuid=%s attempt_count=%s "
                    "error_type=%s",
                    job.job_uuid,
                    job.attempt_count,
                    type(error).__name__,
                )
                continue

            if not recovered:
                stale_count += 1
                LOGGER.info(
                    "dispatcher_recovery_stale job_uuid=%s attempt_count=%s",
                    job.job_uuid,
                    job.attempt_count,
                )
            elif should_retry:
                requeued_count += 1
                LOGGER.debug(
                    "dispatcher_recovery_requeued job_uuid=%s attempt_count=%s",
                    job.job_uuid,
                    job.attempt_count,
                )
            else:
                failed_count += 1
                LOGGER.debug(
                    "dispatcher_recovery_failed job_uuid=%s attempt_count=%s",
                    job.job_uuid,
                    job.attempt_count,
                )

        return RecoveryBatchResult(
            selected_count=len(jobs),
            requeued_count=requeued_count,
            failed_count=failed_count,
            stale_count=stale_count,
            error_count=error_count,
        )

    @staticmethod
    def _validate_configuration(batch_size: int, max_attempts: int) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size doit être un entier strictement positif")
        validate_max_attempts(max_attempts)
