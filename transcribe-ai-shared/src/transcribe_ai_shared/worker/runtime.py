import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final, NoReturn

from transcribe_ai_shared.database.models import JobStatus, JobType
from transcribe_ai_shared.observability import log_event
from transcribe_ai_shared.queue.models import ReceivedJobStreamMessage
from transcribe_ai_shared.queue.protocols import TranscriptionStreams
from transcribe_ai_shared.worker.exceptions import (
    TranscriptionExecutionError,
    WorkerHeartbeatError,
    WorkerJobTypeMismatchError,
    WorkerLeaseLostError,
)
from transcribe_ai_shared.worker.failure_classification import (
    classify_transcription_failure,
)
from transcribe_ai_shared.worker.models import (
    ClaimedJob,
    ClassifiedTranscriptionFailure,
    TranscriptionOutput,
    WorkerClaimDeferred,
    WorkerClaimRejected,
    WorkerCompleted,
    WorkerFailed,
    WorkerIdle,
    WorkerProcessResult,
    WorkerRetryScheduled,
)
from transcribe_ai_shared.worker.protocols import (
    Transcriber,
    TranscriptionCompleter,
    TranscriptionFailureHandler,
    WorkerJobStore,
)


LOGGER = logging.getLogger(__name__)
_WORKER_SERVICES: Final = {
    JobType.FAST: "worker-fast",
    JobType.LONG_FORM_DIARIZATION: "worker-long-form-diarization",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _service_for_job_type(job_type: JobType) -> str:
    """Associe chaque stream métier au service worker qui le consomme."""
    try:
        return _WORKER_SERVICES[job_type]
    except KeyError as error:
        raise ValueError("job_type ne correspond à aucun service worker") from error


class WorkerRuntime:
    """Orchestre le traitement durable d'un message de transcription."""

    def __init__(
        self,
        *,
        streams: TranscriptionStreams,
        job_store: WorkerJobStore,
        transcriber: Transcriber,
        completer: TranscriptionCompleter,
        failure_handler: TranscriptionFailureHandler,
        job_type: JobType,
        group_name: str,
        worker_id: str,
        lease_duration: timedelta,
        heartbeat_interval: timedelta = timedelta(seconds=60),
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not isinstance(job_type, JobType):
            raise ValueError("job_type doit être une valeur JobType")
        self._validate_name(group_name, "group_name")
        self._validate_name(worker_id, "worker_id")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease_duration doit être une durée strictement positive")
        if (
            not isinstance(heartbeat_interval, timedelta)
            or heartbeat_interval <= timedelta(0)
            or heartbeat_interval >= lease_duration
        ):
            raise ValueError(
                "heartbeat_interval doit être strictement positif et inférieur "
                "à lease_duration"
            )

        self._streams = streams
        self._job_store = job_store
        self._transcriber = transcriber
        self._completer = completer
        self._failure_handler = failure_handler
        self._job_type = job_type
        self._service = _service_for_job_type(job_type)
        self._group_name = group_name
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._clock = clock
        self._sleep = sleep
        self._autoclaim_start_id = "0-0"

    async def initialize(self) -> None:
        """Crée idempotemment le groupe du stream attribué à ce worker."""
        await self._streams.ensure_consumer_group(
            self._job_type,
            self._group_name,
        )

    async def process_next(
        self,
        *,
        block_milliseconds: int | None = 5_000,
    ) -> WorkerProcessResult:
        """Traite au plus un nouveau message sans ACK avant sa finalisation métier."""
        message = await self._streams.consume(
            self._job_type,
            self._group_name,
            self._worker_id,
            block_milliseconds=block_milliseconds,
        )
        if message is None:
            return WorkerIdle()

        return await self.process_message(message)

    async def process_next_pending(
        self,
        *,
        min_idle_milliseconds: int,
    ) -> WorkerProcessResult:
        """Récupère au plus un ancien pending sans décider d'un retry métier."""
        claimed = await self._streams.autoclaim(
            self._job_type,
            self._group_name,
            self._worker_id,
            min_idle_milliseconds=min_idle_milliseconds,
            start_id=self._autoclaim_start_id,
            count=1,
        )
        self._autoclaim_start_id = claimed.next_start_id
        if not claimed.messages:
            return WorkerIdle()

        return await self._process_message(
            claimed.messages[0],
            defer_processing_attempt=True,
        )

    async def process_message(
        self,
        message: ReceivedJobStreamMessage,
    ) -> WorkerClaimRejected | WorkerCompleted | WorkerRetryScheduled | WorkerFailed:
        """Traite explicitement un message comme nouveau en respectant COMMIT puis ACK."""
        result = await self._process_message(
            message,
            defer_processing_attempt=False,
        )
        if isinstance(result, WorkerClaimDeferred):
            raise AssertionError("Un nouveau message ne peut pas être différé")
        return result

    async def _process_message(
        self,
        message: ReceivedJobStreamMessage,
        *,
        defer_processing_attempt: bool,
    ) -> (
        WorkerClaimDeferred
        | WorkerClaimRejected
        | WorkerCompleted
        | WorkerRetryScheduled
        | WorkerFailed
    ):
        """Traite un message selon sa provenance nouvelle ou pending."""
        if message.job_type is not self._job_type:
            raise WorkerJobTypeMismatchError(
                message.job_uuid,
                self._job_type,
                message.job_type,
            )

        try:
            claimed_job = await self._job_store.claim(
                message.job_uuid,
                self._worker_id,
                self._lease_expires_at(),
                message.attempt_count,
            )
        except Exception as error:
            self._record_message_event(
                logging.ERROR,
                "claim_failed",
                message,
                dependency="postgresql",
                error_type=type(error).__name__,
            )
            raise
        if claimed_job is None:
            processing_attempt_active = (
                defer_processing_attempt
                and await self._job_store.is_processing_attempt(
                    message.job_uuid,
                    message.attempt_count,
                )
            )
            self._record_message_event(
                logging.INFO,
                "claim_rejected",
                message,
                reason=(
                    "processing_attempt_active"
                    if processing_attempt_active
                    else "postgres_claim_not_granted"
                ),
            )
            if processing_attempt_active:
                return WorkerClaimDeferred(message=message)
            removed = await self._acknowledge_message(message)
            return WorkerClaimRejected(
                message=message,
                removed_from_stream=removed,
            )

        self._record_message_event(
            logging.INFO,
            "job_claimed",
            message,
            attempt_count=claimed_job.attempt_count,
            status=JobStatus.PROCESSING.value,
        )
        try:
            output = await self._transcribe_with_heartbeat(claimed_job, message)
        except TranscriptionExecutionError as error:
            failure = error.failure
        else:
            try:
                await self._completer.complete(
                    job=claimed_job,
                    worker_id=self._worker_id,
                    output=output,
                )
            except Exception as error:
                self._record_message_event(
                    logging.ERROR,
                    "job_completion_failed",
                    message,
                    attempt_count=claimed_job.attempt_count,
                    dependency="postgresql",
                    error_type=type(error).__name__,
                )
                raise
            self._record_message_event(
                logging.INFO,
                "job_completed",
                message,
                attempt_count=claimed_job.attempt_count,
                status=JobStatus.COMPLETED.value,
            )
            removed = await self._acknowledge_message(message)
            return WorkerCompleted(
                message=message,
                job=claimed_job,
                output=output,
                removed_from_stream=removed,
            )

        # Quitter le bloc ``except`` détache les éventuelles erreurs SQL/Redis
        # du message brut de l'exception moteur conservé comme cause interne.
        return await self._resolve_transcription_failure(
            message,
            claimed_job,
            failure,
        )

    async def _resolve_transcription_failure(
        self,
        message: ReceivedJobStreamMessage,
        job: ClaimedJob,
        failure: ClassifiedTranscriptionFailure,
    ) -> WorkerRetryScheduled | WorkerFailed:
        """Persiste l'échec avant de supprimer l'ancien message Redis."""
        try:
            resolution = await self._failure_handler.handle(
                job=job,
                worker_id=self._worker_id,
                failure=failure,
            )
        except Exception as error:
            self._record_message_event(
                logging.ERROR,
                "failure_transition_failed",
                message,
                attempt_count=job.attempt_count,
                dependency="postgresql",
                error_type=type(error).__name__,
                failure_category=failure.category.value,
                failure_code=failure.error_code,
            )
            raise

        if resolution.status not in {JobStatus.QUEUED, JobStatus.FAILED}:
            raise ValueError(
                "Le gestionnaire d'échec doit retourner un statut QUEUED ou FAILED"
            )

        if resolution.status is JobStatus.QUEUED:
            self._record_message_event(
                logging.INFO,
                "retry_scheduled",
                message,
                attempt_count=job.attempt_count,
                failure_category=failure.category.value,
                failure_code=failure.error_code,
                next_attempt_count=resolution.attempt_count,
                status=JobStatus.QUEUED.value,
            )
            removed = await self._acknowledge_message(message)
            return WorkerRetryScheduled(
                message=message,
                job=job,
                failure=failure,
                next_attempt_count=resolution.attempt_count,
                removed_from_stream=removed,
            )
        self._record_message_event(
            logging.ERROR,
            "job_failed",
            message,
            attempt_count=job.attempt_count,
            failure_category=failure.category.value,
            failure_code=failure.error_code,
            status=JobStatus.FAILED.value,
        )
        removed = await self._acknowledge_message(message)
        return WorkerFailed(
            message=message,
            job=job,
            failure=failure,
            removed_from_stream=removed,
        )

    async def _transcribe_with_heartbeat(
        self,
        job: ClaimedJob,
        message: ReceivedJobStreamMessage,
    ) -> TranscriptionOutput:
        """Exécute l'inférence tant que la tentative conserve un lease valide."""
        self._record_message_event(
            logging.INFO,
            "transcription_started",
            message,
            attempt_count=job.attempt_count,
            status=JobStatus.PROCESSING.value,
        )
        transcription_task = asyncio.create_task(
            self._transcribe(job),
            name=f"transcription-{job.job_uuid}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(job, message),
            name=f"heartbeat-{job.job_uuid}",
        )

        try:
            done, _ = await asyncio.wait(
                {transcription_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            # Annuler les deux enfants avant le premier await garantit qu'une
            # seconde annulation du parent ne peut pas laisser l'autre tâche
            # tourner en arrière-plan.
            transcription_task.cancel()
            heartbeat_task.cancel()
            await self._drain_task(transcription_task)
            await self._drain_task(heartbeat_task)
            raise

        if transcription_task in done:
            try:
                output = await transcription_task
            except BaseException as error:
                await self._settle_task(heartbeat_task, cancel=True)
                self._raise_transcription_failure(error, job, message)

            heartbeat_error = await self._settle_task(
                heartbeat_task,
                cancel=True,
            )
            if heartbeat_error is not None:
                raise heartbeat_error
        else:
            heartbeat_error = await self._settle_task(
                heartbeat_task,
                cancel=False,
            )
            transcription_error = await self._settle_task(
                transcription_task,
                cancel=True,
            )
            if transcription_error is not None:
                self._raise_transcription_failure(
                    transcription_error,
                    job,
                    message,
                )
            if heartbeat_error is None:
                heartbeat_error = WorkerHeartbeatError(
                    job.job_uuid,
                    self._worker_id,
                    job.attempt_count,
                )
            raise heartbeat_error

        # Ce dernier CAS couvre une boucle événementielle restée bloquée entre
        # deux ticks et redonne une fenêtre complète à la transaction terminale.
        await self._renew_lease_or_raise(job, message)
        return output

    async def _transcribe(self, job: ClaimedJob) -> TranscriptionOutput:
        output = await self._transcriber.transcribe(job.audio_location)
        if not isinstance(output, TranscriptionOutput):
            raise TypeError("transcribe doit retourner un TranscriptionOutput")
        return output

    async def _heartbeat(
        self,
        job: ClaimedJob,
        message: ReceivedJobStreamMessage,
    ) -> None:
        while True:
            await self._sleep(self._heartbeat_interval.total_seconds())
            await self._renew_lease_or_raise(job, message)

    async def _renew_lease_or_raise(
        self,
        job: ClaimedJob,
        message: ReceivedJobStreamMessage,
    ) -> None:
        try:
            renewed = await self._job_store.renew_lease(
                job.job_uuid,
                self._worker_id,
                self._lease_expires_at(),
                job.attempt_count,
            )
        except Exception as error:
            self._record_message_event(
                logging.ERROR,
                "heartbeat_failed",
                message,
                attempt_count=job.attempt_count,
                dependency="postgresql",
                error_type=type(error).__name__,
            )
            raise WorkerHeartbeatError(
                job.job_uuid,
                self._worker_id,
                job.attempt_count,
            ) from error
        if not renewed:
            self._record_message_event(
                logging.ERROR,
                "heartbeat_lost",
                message,
                attempt_count=job.attempt_count,
            )
            raise WorkerLeaseLostError(
                job.job_uuid,
                self._worker_id,
                job.attempt_count,
            )

    async def _acknowledge_message(
        self,
        message: ReceivedJobStreamMessage,
    ) -> bool:
        """Journalise l'ACK seulement lorsque Redis confirme sa suppression."""
        try:
            removed = await self._streams.ack_and_delete(
                self._group_name,
                message,
            )
        except Exception as error:
            self._record_message_event(
                logging.ERROR,
                "redis_message_ack_failed",
                message,
                dependency="redis",
                error_type=type(error).__name__,
            )
            raise
        if removed:
            self._record_message_event(
                logging.INFO,
                "redis_message_acked",
                message,
            )
        return removed

    def _record_message_event(
        self,
        level: int,
        event: str,
        message: ReceivedJobStreamMessage,
        *,
        attempt_count: int | None = None,
        **fields: object,
    ) -> None:
        """Ajoute les corrélations communes sans exposer le payload du job."""
        log_event(
            LOGGER,
            level,
            service=self._service,
            event=event,
            job_uuid=message.job_uuid,
            attempt_count=(
                message.attempt_count if attempt_count is None else attempt_count
            ),
            worker_id=self._worker_id,
            redis_message_id=message.redis_message_id,
            job_type=self._job_type.value,
            **fields,
        )

    @staticmethod
    async def _settle_task(
        task: asyncio.Task[object],
        *,
        cancel: bool,
    ) -> BaseException | None:
        """Attend une tâche et distingue son annulation de celle du nettoyage."""
        cancelled_by_cleanup = cancel and not task.done()
        if cancelled_by_cleanup:
            task.cancel()
        current_task = asyncio.current_task()
        cancellation_count = current_task.cancelling() if current_task else 0
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            externally_cancelled = (
                current_task is not None
                and current_task.cancelling() > cancellation_count
            )
            if externally_cancelled:
                if not task.done():
                    task.cancel()
                await WorkerRuntime._drain_task(task)
                raise
            return None if cancelled_by_cleanup else error
        except BaseException as error:
            return error
        return None

    @staticmethod
    async def _drain_task(task: asyncio.Task[object]) -> None:
        """Draine un enfant même si l'appelant reçoit une seconde annulation."""
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                return
        try:
            task.result()
        except BaseException:
            pass

    @staticmethod
    def _raise_transcription_failure(
        error: BaseException,
        job: ClaimedJob,
        message: ReceivedJobStreamMessage,
    ) -> NoReturn:
        if isinstance(error, Exception):
            raise TranscriptionExecutionError(
                job.job_uuid,
                message.redis_message_id,
                classify_transcription_failure(error),
            ) from error
        raise error

    def _lease_expires_at(self) -> datetime:
        """Calcule une échéance UTC à partir d'une horloge testable."""
        current_time = self._clock()
        if (
            not isinstance(current_time, datetime)
            or current_time.tzinfo is None
            or current_time.utcoffset() is None
        ):
            raise ValueError("clock doit retourner une date avec fuseau horaire")
        return current_time.astimezone(UTC) + self._lease_duration

    @staticmethod
    def _validate_name(value: str, parameter_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{parameter_name} ne peut pas être vide")
