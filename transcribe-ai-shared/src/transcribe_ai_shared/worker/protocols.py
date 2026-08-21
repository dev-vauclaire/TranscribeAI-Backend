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
    ) -> ClaimedJob | None:
        """Réserve atomiquement un job ou refuse un message devenu obsolète."""
        ...
