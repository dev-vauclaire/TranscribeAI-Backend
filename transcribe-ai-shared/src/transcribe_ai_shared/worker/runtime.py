import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.queue.models import ReceivedJobStreamMessage
from transcribe_ai_shared.queue.protocols import TranscriptionStreams
from transcribe_ai_shared.worker.exceptions import (
    TranscriptionExecutionError,
    WorkerHeartbeatError,
    WorkerJobTypeMismatchError,
    WorkerLeaseLostError,
)
from transcribe_ai_shared.worker.models import (
    ClaimedJob,
    TranscriptionOutput,
    WorkerClaimRejected,
    WorkerCompleted,
    WorkerIdle,
    WorkerProcessResult,
)
from transcribe_ai_shared.worker.protocols import (
    Transcriber,
    TranscriptionCompleter,
    WorkerJobStore,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WorkerRuntime:
    """Orchestre le traitement durable d'un message de transcription."""

    def __init__(
        self,
        *,
        streams: TranscriptionStreams,
        job_store: WorkerJobStore,
        transcriber: Transcriber,
        completer: TranscriptionCompleter,
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
        self._job_type = job_type
        self._group_name = group_name
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._clock = clock
        self._sleep = sleep

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

    async def process_message(
        self,
        message: ReceivedJobStreamMessage,
    ) -> WorkerClaimRejected | WorkerCompleted:
        """Traite un message nouveau ou récupéré en respectant COMMIT puis ACK."""
        if message.job_type is not self._job_type:
            raise WorkerJobTypeMismatchError(
                message.job_uuid,
                self._job_type,
                message.job_type,
            )

        claimed_job = await self._job_store.claim(
            message.job_uuid,
            self._worker_id,
            self._lease_expires_at(),
            message.attempt_count,
        )
        if claimed_job is None:
            removed = await self._streams.ack_and_delete(
                self._group_name,
                message,
            )
            return WorkerClaimRejected(
                message=message,
                removed_from_stream=removed,
            )

        output = await self._transcribe_with_heartbeat(claimed_job, message)

        await self._completer.complete(
            job=claimed_job,
            worker_id=self._worker_id,
            output=output,
        )
        removed = await self._streams.ack_and_delete(
            self._group_name,
            message,
        )
        return WorkerCompleted(
            message=message,
            job=claimed_job,
            output=output,
            removed_from_stream=removed,
        )

    async def _transcribe_with_heartbeat(
        self,
        job: ClaimedJob,
        message: ReceivedJobStreamMessage,
    ) -> TranscriptionOutput:
        """Exécute l'inférence tant que la tentative conserve un lease valide."""
        transcription_task = asyncio.create_task(
            self._transcribe(job),
            name=f"transcription-{job.job_uuid}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat(job),
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
        await self._renew_lease_or_raise(job)
        return output

    async def _transcribe(self, job: ClaimedJob) -> TranscriptionOutput:
        output = await self._transcriber.transcribe(job.audio_location)
        if not isinstance(output, TranscriptionOutput):
            raise TypeError("transcribe doit retourner un TranscriptionOutput")
        return output

    async def _heartbeat(self, job: ClaimedJob) -> None:
        while True:
            await self._sleep(self._heartbeat_interval.total_seconds())
            await self._renew_lease_or_raise(job)

    async def _renew_lease_or_raise(self, job: ClaimedJob) -> None:
        try:
            renewed = await self._job_store.renew_lease(
                job.job_uuid,
                self._worker_id,
                self._lease_expires_at(),
                job.attempt_count,
            )
        except Exception as error:
            raise WorkerHeartbeatError(
                job.job_uuid,
                self._worker_id,
                job.attempt_count,
            ) from error
        if not renewed:
            raise WorkerLeaseLostError(
                job.job_uuid,
                self._worker_id,
                job.attempt_count,
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
