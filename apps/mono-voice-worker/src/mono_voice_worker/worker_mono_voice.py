import time
from datetime import datetime, timezone
from uuid import UUID

from mono_voice_worker.client_whisper import (
    ClientWhisper,
    WhisperClientError,
    WhisperPayload,
)
from mono_voice_worker.config import WorkerMonoVoiceSettings
from transcribe_ai_shared import (
    AudioManager,
    JobRepository,
    JobStatus,
    RedisQueueService,
    SessionFactory,
    transaction,
)

# TODO : Gérer le cas où la Base de données est indisponible --> backoff et retry


class JobNotFoundError(Exception):
    """Erreur produite lorsque le job dépilé n'existe pas en base."""


class WorkerMonoVoice:
    def __init__(
        self,
        settings: WorkerMonoVoiceSettings,
        session_factory: SessionFactory,
        redis_queue_service: RedisQueueService,
        client_whisper: ClientWhisper,
    ) -> None:
        self.settings = settings
        self.redis_queue_service = redis_queue_service
        self.session_factory = session_factory
        self.audio_manager = AudioManager(settings.audio_folder_path)
        self.whisper_client = client_whisper

    def run_once(self) -> None:
        job_id: int | None = None
        job_filename: str | None = None
        job_uuid: UUID | None = None

        try:
            raw_job_uuid = self.redis_queue_service.pop_job()
            if raw_job_uuid is None:
                return

            try:
                job_uuid = UUID(raw_job_uuid)
            except ValueError:
                print("Message Redis ignoré : identifiant de job invalide.")
                return

            print(f"Traitement du job {job_uuid}...")

            with transaction(self.session_factory) as session:
                repository = JobRepository(session)
                job = repository.get_by_uuid(job_uuid)

                if job is None:
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
                print(f"Job {job_id} complété avec succès.")

        except (JobNotFoundError, FileNotFoundError, WhisperClientError) as error:
            if job_id is not None:
                with transaction(self.session_factory) as session:
                    repository = JobRepository(session)
                    repository.fail_job(
                        job_id,
                        error_msg=str(error),
                        ended_at=datetime.now(timezone.utc),
                    )
            print(f"Erreur lors du traitement du job {job_uuid}: {error}")

        finally:
            if job_filename is not None:
                self.audio_manager.delete_audio(job_filename)

        time.sleep(self.settings.worker_loop_sleep_time)

    def __str__(self) -> str:
        return f"WorkerMonoVoice(settings={self.settings})"
