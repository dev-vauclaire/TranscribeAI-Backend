from contextlib import AbstractContextManager
from typing import BinaryIO, Protocol
from uuid import UUID

from transcribe_ai_shared.storage.models import (
    AudioLocation,
    StorageScanResult,
    TranscriptionDirectory,
)


class AudioStorage(Protocol):
    """Contrat synchrone de stockage, indépendant du backend utilisé.

    Les implémentations traduisent les collisions, absences et localisations
    invalides vers les exceptions publiques du sous-package ``storage``.
    """

    def save(
        self,
        job_uuid: UUID,
        source: BinaryIO,
        *,
        extension: str,
    ) -> AudioLocation:
        """Stocke le flux sans écraser l'entrée déjà associée au job."""
        ...

    def open(
        self,
        location: AudioLocation,
    ) -> AbstractContextManager[BinaryIO]:
        """Retourne un contexte qui ouvre puis ferme le flux binaire."""
        ...

    def delete(self, location: AudioLocation) -> None:
        """Supprime l'audio de manière idempotente."""
        ...


class AudioStorageMaintenance(Protocol):
    """Contrat minimal utilisé par les tâches de maintenance du stockage."""

    def scan_transcription_directories(self) -> StorageScanResult:
        """Inventorie les dossiers UUID sûrs et agrège les entrées ignorées."""
        ...

    def delete_transcription_directory(
        self,
        directory: TranscriptionDirectory,
    ) -> bool:
        """Supprime un dossier inchangé ; retourne ``False`` s'il a disparu."""
        ...
