from datetime import datetime
import logging
from enum import Enum
from zoneinfo import ZoneInfo
from uuid import UUID

from mono_voice_worker.client_whisper import (
    ClientWhisper,
    WhisperClientError,
    WhisperPayload,
)
from transcribe_ai_shared import (
    AudioManager,
    WrongAudioPathError,
    JobRepository,
    JobStatus,
    RedisQueueService,
    SessionFactory,
    transaction,
)


class JobNotFoundError(Exception):
    """Erreur produite lorsque le job dépilé n'existe pas en base."""


class WorkerPayloadStatus(Enum):
    IDLE = "idle"
    INVALID_JOB_UUID = "invalid_job_uuid"
    JOB_NOT_FOUND = "job_not_found"
    FAILED = "failed"
    COMPLETED = "completed"


class WorkerPayload:
    status: WorkerPayloadStatus
    job_uuid: UUID | None
    date: datetime
    error_message: str | None

    def __str__(self) -> str:
        return f"WorkerPayload(status={self.status}, job_uuid={self.job_uuid}, date={self.date}, error_message={self.error_message})"


class WorkerMonoVoice:
    def __init__(
        self,
        session_factory: SessionFactory,
        redis_queue_service: RedisQueueService,
        client_whisper: ClientWhisper,
        audio_manager: AudioManager,
    ) -> None:
        self.redis_queue_service = redis_queue_service
        self.session_factory = session_factory
        self.audio_manager = audio_manager
        self.whisper_client = client_whisper

    def run_once(self) -> WorkerPayload:
        job_id: int | None = None
        job_filename: str | None = None
        job_uuid: UUID | None = None

        worker_payload = WorkerPayload()
        worker_payload.status = WorkerPayloadStatus.IDLE
        worker_payload.job_uuid = job_uuid
        worker_payload.date = datetime.now(ZoneInfo("Europe/Paris"))
        worker_payload.error_message = None

        try:
            raw_job_uuid = self.redis_queue_service.pop_job()
            if raw_job_uuid is None:
                return worker_payload

            try:
                job_uuid = UUID(raw_job_uuid)
                worker_payload.job_uuid = job_uuid
                worker_payload.date = datetime.now(ZoneInfo("Europe/Paris"))
            except ValueError:
                logging.warning("Message Redis ignoré : identifiant de job invalide.")
                worker_payload.status = WorkerPayloadStatus.INVALID_JOB_UUID
                worker_payload.error_message = "Identifiant de job invalide"
                return worker_payload

            logging.info(f"Traitement du job {job_uuid}...")

            with transaction(self.session_factory) as session:
                repository = JobRepository(session)
                job = repository.get_by_uuid(job_uuid)

                if job is None:
                    worker_payload.status = WorkerPayloadStatus.JOB_NOT_FOUND
                    worker_payload.error_message = (
                        f"Le job {job_uuid} n'existe pas en base de données"
                    )
                    raise JobNotFoundError(f"Le job {job_uuid} n'existe pas")

                repository.update_status(job.id, JobStatus.PROCESSING)

                job_id = job.id
                job_filename = job.filename

            with self.audio_manager.open_audio(job_filename) as audio_file:
                whisper_payload: WhisperPayload = (
                    self.whisper_client.send_to_whisper_service(
                        audio_file,
                        filename=job_filename,
                    )
                )

            with transaction(self.session_factory) as session:
                repository = JobRepository(session)
                repository.complete_job(
                    job_id,
                    result_data=whisper_payload.model_dump(mode="json"),
                )

            worker_payload.status = WorkerPayloadStatus.COMPLETED

        except (JobNotFoundError, WrongAudioPathError, WhisperClientError) as error:
            if job_id is not None:
                with transaction(self.session_factory) as session:
                    repository = JobRepository(session)
                    repository.fail_job(
                        job_id,
                        error_msg=str(error),
                        ended_at=datetime.now(ZoneInfo("Europe/Paris")),
                    )
            worker_payload.status = WorkerPayloadStatus.FAILED
            worker_payload.error_message = str(error)

        finally:
            if job_filename is not None:
                self.audio_manager.delete_audio(job_filename)

        return worker_payload
