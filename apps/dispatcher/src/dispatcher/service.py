import logging

from dispatcher.models import DispatchBatchResult, DispatchJobSnapshot
from dispatcher.protocols import DispatchJobStore
from transcribe_ai_shared import JobStreamMessage, TranscriptionStreams
from transcribe_ai_shared.observability import log_event


LOGGER = logging.getLogger(__name__)
SERVICE = "dispatcher"


class DispatcherService:
    """Publie au plus un batch de jobs et confirme chaque publication."""

    def __init__(
        self,
        *,
        job_store: DispatchJobStore,
        streams: TranscriptionStreams,
    ) -> None:
        self._job_store = job_store
        self._streams = streams

    async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
        """Exécute un batch one-shot sans interrompre les jobs suivants sur erreur."""
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size doit être un entier strictement positif")

        jobs = await self._job_store.find_jobs_requiring_dispatch(batch_size)
        published_count = 0
        confirmed_count = 0
        stale_count = 0
        error_count = 0

        for job in jobs:
            log_event(
                LOGGER,
                logging.INFO,
                service=SERVICE,
                event="dispatch_started",
                job_uuid=job.job_uuid,
                attempt_count=job.attempt_count,
                job_type=job.job_type,
            )
            try:
                redis_message_id = await self._streams.publish(
                    self._message_from_snapshot(job)
                )
            except Exception as error:
                error_count += 1
                log_event(
                    LOGGER,
                    logging.WARNING,
                    service=SERVICE,
                    event="dispatch_failed",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    job_type=job.job_type,
                    dependency="redis",
                    reason="publication_error",
                    error_type=type(error).__name__,
                )
                continue

            published_count += 1
            log_event(
                LOGGER,
                logging.INFO,
                service=SERVICE,
                event="redis_published",
                job_uuid=job.job_uuid,
                attempt_count=job.attempt_count,
                redis_message_id=redis_message_id,
                job_type=job.job_type,
                action="redis_published",
            )

            try:
                confirmed = await self._job_store.mark_dispatched(job)
            except Exception as error:
                error_count += 1
                log_event(
                    LOGGER,
                    logging.WARNING,
                    service=SERVICE,
                    event="dispatch_failed",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    redis_message_id=redis_message_id,
                    job_type=job.job_type,
                    dependency="postgresql",
                    reason="confirmation_error",
                    error_type=type(error).__name__,
                )
                continue

            if confirmed:
                confirmed_count += 1
                log_event(
                    LOGGER,
                    logging.INFO,
                    service=SERVICE,
                    event="dispatched",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    redis_message_id=redis_message_id,
                    job_type=job.job_type,
                    action="postgresql_confirmed",
                )
            else:
                stale_count += 1
                log_event(
                    LOGGER,
                    logging.INFO,
                    service=SERVICE,
                    event="dispatch_confirmation_rejected",
                    job_uuid=job.job_uuid,
                    attempt_count=job.attempt_count,
                    redis_message_id=redis_message_id,
                    job_type=job.job_type,
                    reason="stale_snapshot",
                )

        return DispatchBatchResult(
            selected_count=len(jobs),
            published_count=published_count,
            confirmed_count=confirmed_count,
            stale_count=stale_count,
            error_count=error_count,
        )

    @staticmethod
    def _message_from_snapshot(snapshot: DispatchJobSnapshot) -> JobStreamMessage:
        """N'expose dans Redis que le contrat nécessaire aux workers."""
        return JobStreamMessage(
            job_uuid=snapshot.job_uuid,
            job_type=snapshot.job_type,
            attempt_count=snapshot.attempt_count,
        )
