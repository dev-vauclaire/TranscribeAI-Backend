from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.queue.protocols import TranscriptionStreams
from transcribe_ai_shared.worker.exceptions import TranscriptionExecutionError
from transcribe_ai_shared.worker.models import (
    TranscriptionOutput,
    WorkerClaimRejected,
    WorkerIdle,
    WorkerProcessResult,
    WorkerTranscribed,
)
from transcribe_ai_shared.worker.protocols import Transcriber, WorkerJobStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WorkerRuntime:
    """Orchestre une consommation Redis, un claim durable et une transcription."""

    def __init__(
        self,
        *,
        streams: TranscriptionStreams,
        job_store: WorkerJobStore,
        transcriber: Transcriber,
        job_type: JobType,
        group_name: str,
        worker_id: str,
        lease_duration: timedelta,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(job_type, JobType):
            raise ValueError("job_type doit être une valeur JobType")
        self._validate_name(group_name, "group_name")
        self._validate_name(worker_id, "worker_id")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease_duration doit être une durée strictement positive")

        self._streams = streams
        self._job_store = job_store
        self._transcriber = transcriber
        self._job_type = job_type
        self._group_name = group_name
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._clock = clock

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

        claimed_job = await self._job_store.claim(
            message.job_uuid,
            self._worker_id,
            self._lease_expires_at(),
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

        try:
            output = await self._transcriber.transcribe(
                claimed_job.audio_location,
            )
            if not isinstance(output, TranscriptionOutput):
                raise TypeError("transcribe doit retourner un TranscriptionOutput")
        except Exception as error:
            raise TranscriptionExecutionError(
                claimed_job.job_uuid,
                message.redis_message_id,
            ) from error

        return WorkerTranscribed(
            message=message,
            job=claimed_job,
            output=output,
        )

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
