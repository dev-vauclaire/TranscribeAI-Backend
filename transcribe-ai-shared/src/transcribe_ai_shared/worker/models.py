from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.queue.models import ReceivedJobStreamMessage
from transcribe_ai_shared.storage.models import AudioLocation


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


@dataclass(frozen=True, slots=True)
class WorkerIdle:
    """Indique qu'aucun nouveau message Redis n'était disponible."""


@dataclass(frozen=True, slots=True)
class WorkerClaimRejected:
    """Indique qu'un message obsolète n'a pas obtenu le claim PostgreSQL."""

    message: ReceivedJobStreamMessage
    removed_from_stream: bool


@dataclass(frozen=True, slots=True)
class WorkerTranscribed:
    """Conserve le contexte requis par la future étape de finalisation atomique."""

    message: ReceivedJobStreamMessage
    job: ClaimedJob
    output: TranscriptionOutput


type WorkerProcessResult = WorkerIdle | WorkerClaimRejected | WorkerTranscribed
