from uuid import UUID

from transcribe_ai_shared.database.models import JobType


class TranscriptionExecutionError(RuntimeError):
    """Encapsule une panne du transcriber sans perdre son exception d'origine."""

    def __init__(
        self,
        job_uuid: UUID,
        redis_message_id: str,
    ) -> None:
        self.job_uuid = job_uuid
        self.redis_message_id = redis_message_id
        super().__init__(
            f"La transcription du job {job_uuid} issue du message "
            f"Redis {redis_message_id} a échoué."
        )


class WorkerJobTypeMismatchError(RuntimeError):
    """Signale qu'un job ne correspond pas au stream attribué au worker."""

    def __init__(
        self,
        job_uuid: UUID,
        expected_job_type: JobType,
        actual_job_type: JobType,
    ) -> None:
        self.job_uuid = job_uuid
        self.expected_job_type = expected_job_type
        self.actual_job_type = actual_job_type
        super().__init__(
            f"Le job {job_uuid} est de type {actual_job_type.value}, "
            f"mais le worker consomme le stream {expected_job_type.value}."
        )
