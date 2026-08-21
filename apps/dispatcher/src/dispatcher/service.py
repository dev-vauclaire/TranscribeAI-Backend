from collections.abc import Callable
from datetime import UTC, datetime
import logging

from dispatcher.models import DispatchBatchResult
from dispatcher.protocols import DispatchJobStore
from transcribe_ai_shared import TranscriptionStreams


LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DispatcherService:
    """Publie au plus un batch de jobs et confirme chaque publication."""

    def __init__(
        self,
        *,
        job_store: DispatchJobStore,
        streams: TranscriptionStreams,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._job_store = job_store
        self._streams = streams
        self._clock = clock

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
                redis_message_id = await self._streams.publish(job)
                published_count += 1
                dispatched_at = self._current_time()
                confirmed = await self._job_store.mark_dispatched(
                    job.job_uuid,
                    job.attempt_count,
                    dispatched_at,
                )
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

    def _current_time(self) -> datetime:
        """Normalise l'horloge injectée en UTC avant l'écriture TIMESTAMPTZ."""
        current_time = self._clock()
        if (
            not isinstance(current_time, datetime)
            or current_time.tzinfo is None
            or current_time.utcoffset() is None
        ):
            raise ValueError("clock doit retourner une date avec fuseau horaire")
        return current_time.astimezone(UTC)
