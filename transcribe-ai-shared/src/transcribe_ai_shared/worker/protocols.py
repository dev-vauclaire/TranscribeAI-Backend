from datetime import datetime
from typing import Protocol
from uuid import UUID

from transcribe_ai_shared.storage.models import AudioLocation
from transcribe_ai_shared.worker.models import ClaimedJob, TranscriptionOutput


class Transcriber(Protocol):
    """Moteur de transcription asynchrone interchangeable entre les workers."""

    async def transcribe(
        self,
        audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        """Produit une transcription à partir d'une localisation audio canonique."""
        ...


class WorkerJobStore(Protocol):
    """Port PostgreSQL minimal nécessaire au runtime worker."""

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> ClaimedJob | None:
        """Réserve atomiquement un job ou refuse un message devenu obsolète."""
        ...

    async def renew_lease(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> bool:
        """Prolonge la tentative encore détenue ou signale sa perte."""
        ...


class TranscriptionCompleter(Protocol):
    """Port de finalisation durable appelé après une inférence réussie."""

    async def complete(
        self,
        *,
        job: ClaimedJob,
        worker_id: str,
        output: TranscriptionOutput,
    ) -> None:
        """Persiste le résultat et l'état terminal avant tout ACK Redis."""
        ...
