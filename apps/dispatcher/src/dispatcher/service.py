import logging

from dispatcher.models import DispatchBatchResult, DispatchJobSnapshot
from dispatcher.protocols import DispatchJobStore
from transcribe_ai_shared import JobStreamMessage, TranscriptionStreams


LOGGER = logging.getLogger(__name__)


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
            redis_message_id: str | None = None
            try:
                redis_message_id = await self._streams.publish(
                    self._message_from_snapshot(job)
                )
                published_count += 1
                confirmed = await self._job_store.mark_dispatched(job)
            except Exception as error:
                error_count += 1
                LOGGER.warning(
                    "dispatcher_job_error job_uuid=%s job_type=%s "
                    "attempt_count=%s redis_message_id=%s error_type=%s",
                    job.job_uuid,
                    job.job_type.value,
                    job.attempt_count,
                    redis_message_id,
                    type(error).__name__,
                )
                continue

            if confirmed:
                confirmed_count += 1
                LOGGER.debug(
                    "dispatcher_job_confirmed job_uuid=%s job_type=%s "
                    "attempt_count=%s redis_message_id=%s",
                    job.job_uuid,
                    job.job_type.value,
                    job.attempt_count,
                    redis_message_id,
                )
            else:
                stale_count += 1
                LOGGER.info(
                    "dispatcher_job_stale job_uuid=%s job_type=%s "
                    "attempt_count=%s redis_message_id=%s",
                    job.job_uuid,
                    job.job_type.value,
                    job.attempt_count,
                    redis_message_id,
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
