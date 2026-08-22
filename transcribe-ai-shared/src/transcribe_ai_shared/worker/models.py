from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue

from transcribe_ai_shared.database.models import JobStatus, JobType
from transcribe_ai_shared.queue.models import ReceivedJobStreamMessage
from transcribe_ai_shared.storage.models import AudioLocation
from transcribe_ai_shared.worker.failure_code import (
    normalize_transcription_error_code,
)


@dataclass(frozen=True, slots=True)
class TranscriptionOutput:
    """Sortie métier indépendante d'un moteur de transcription particulier."""

    result: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Snapshot détaché d'un job réservé durablement dans PostgreSQL."""

    job_uuid: UUID
    job_type: JobType
    attempt_count: int
    audio_location: AudioLocation


class TranscriptionFailureCategory(StrEnum):
    """Nature métier d'un échec produit par le moteur de transcription."""

    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"


@dataclass(frozen=True, slots=True)
class ClassifiedTranscriptionFailure:
    """Échec classifié sans détail sensible destiné à PostgreSQL."""

    category: TranscriptionFailureCategory
    error_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, TranscriptionFailureCategory):
            raise TypeError(
                "category doit être une valeur TranscriptionFailureCategory"
            )
        object.__setattr__(
            self,
            "error_code",
            normalize_transcription_error_code(self.error_code),
        )


@dataclass(frozen=True, slots=True)
class TranscriptionFailureResolution:
    """État PostgreSQL durable obtenu après résolution d'un échec."""

    status: JobStatus
    attempt_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, JobStatus) or self.status not in {
            JobStatus.QUEUED,
            JobStatus.FAILED,
        }:
            raise ValueError("status doit être QUEUED ou FAILED")
        if type(self.attempt_count) is not int or self.attempt_count < 0:
            raise ValueError("attempt_count doit être un entier positif ou nul")


@dataclass(frozen=True, slots=True)
class WorkerIdle:
    """Indique qu'aucun nouveau message Redis n'était disponible."""


@dataclass(frozen=True, slots=True)
class WorkerClaimRejected:
    """Indique qu'un message obsolète n'a pas obtenu le claim PostgreSQL."""

    message: ReceivedJobStreamMessage
    removed_from_stream: bool


@dataclass(frozen=True, slots=True)
class WorkerCompleted:
    """Indique que le résultat est durable après la tentative d'ACK Redis."""

    message: ReceivedJobStreamMessage
    job: ClaimedJob
    output: TranscriptionOutput
    removed_from_stream: bool


@dataclass(frozen=True, slots=True)
class WorkerRetryScheduled:
    """Indique que PostgreSQL a durablement programmé une nouvelle tentative."""

    message: ReceivedJobStreamMessage
    job: ClaimedJob
    failure: ClassifiedTranscriptionFailure
    next_attempt_count: int
    removed_from_stream: bool


@dataclass(frozen=True, slots=True)
class WorkerFailed:
    """Indique que PostgreSQL a durablement placé le job en échec terminal."""

    message: ReceivedJobStreamMessage
    job: ClaimedJob
    failure: ClassifiedTranscriptionFailure
    removed_from_stream: bool


type WorkerProcessResult = (
    WorkerIdle
    | WorkerClaimRejected
    | WorkerCompleted
    | WorkerRetryScheduled
    | WorkerFailed
)
