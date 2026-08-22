from uuid import UUID

from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.worker.failure_code import (
    normalize_transcription_error_code,
)
from transcribe_ai_shared.worker.models import ClassifiedTranscriptionFailure


class RetryableTranscriptionError(RuntimeError):
    """Erreur moteur transitoire pouvant faire l'objet d'une nouvelle tentative."""

    def __init__(self, error_code: str) -> None:
        self.error_code = normalize_transcription_error_code(error_code)
        super().__init__(
            f"La transcription a rencontré l'erreur retryable {self.error_code}."
        )


class PermanentTranscriptionError(RuntimeError):
    """Erreur moteur déterministe qui ne doit pas être rejouée."""

    def __init__(self, error_code: str) -> None:
        self.error_code = normalize_transcription_error_code(error_code)
        super().__init__(
            f"La transcription a rencontré l'erreur permanente {self.error_code}."
        )


class TranscriptionExecutionError(RuntimeError):
    """Encapsule une panne du transcriber sans perdre son exception d'origine."""

    def __init__(
        self,
        job_uuid: UUID,
        redis_message_id: str,
        failure: ClassifiedTranscriptionFailure,
    ) -> None:
        self.job_uuid = job_uuid
        self.redis_message_id = redis_message_id
        self.failure = failure
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


class WorkerHeartbeatError(RuntimeError):
    """Encapsule une panne technique pendant le renouvellement du lease."""

    def __init__(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> None:
        self.job_uuid = job_uuid
        self.worker_id = worker_id
        self.expected_attempt_count = expected_attempt_count
        super().__init__(f"Le heartbeat du job {job_uuid} a échoué.")


class WorkerLeaseLostError(RuntimeError):
    """Signale que le worker ne possède plus la tentative en cours."""

    def __init__(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> None:
        self.job_uuid = job_uuid
        self.worker_id = worker_id
        self.expected_attempt_count = expected_attempt_count
        super().__init__(
            f"Le worker {worker_id} a perdu le lease du job {job_uuid} "
            f"pour la tentative {expected_attempt_count}."
        )


class TranscriptionCompletionError(RuntimeError):
    """Signale une panne SQLAlchemy pendant la finalisation atomique d'un job."""

    def __init__(self, job_uuid: UUID) -> None:
        self.job_uuid = job_uuid
        super().__init__(f"La finalisation du job {job_uuid} a échoué.")


class TranscriptionCompletionRejectedError(RuntimeError):
    """Signale que le worker ne possède plus la tentative à finaliser."""

    def __init__(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> None:
        self.worker_id = worker_id
        self.expected_attempt_count = expected_attempt_count
        self.job_uuid = job_uuid
        super().__init__(
            f"La finalisation du job {job_uuid} a été refusée pour le worker "
            f"{worker_id} et la tentative {expected_attempt_count}."
        )


class TranscriptionFailureTransitionError(RuntimeError):
    """Signale une panne SQLAlchemy pendant la résolution durable d'un échec."""

    def __init__(self, job_uuid: UUID) -> None:
        self.job_uuid = job_uuid
        super().__init__(f"La résolution de l'échec du job {job_uuid} a échoué.")


class TranscriptionFailureTransitionRejectedError(RuntimeError):
    """Signale que le worker ne possède plus la tentative à faire évoluer."""

    def __init__(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> None:
        self.job_uuid = job_uuid
        self.worker_id = worker_id
        self.expected_attempt_count = expected_attempt_count
        super().__init__(
            f"La résolution de l'échec du job {job_uuid} a été refusée pour "
            f"le worker {worker_id} et la tentative {expected_attempt_count}."
        )
