from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID

from transcribe_ai_shared.storage.exceptions import InvalidAudioLocationError


@dataclass(frozen=True, slots=True)
class AudioLocation:
    """Clé canonique relative permettant de retrouver l'entrée audio d'un job."""

    uri: str

    def __str__(self) -> str:
        return self.uri

    @property
    def job_uuid(self) -> UUID:
        """Retourne l'identifiant du job encodé dans la clé de stockage."""
        return UUID(PurePosixPath(self.uri).parts[0])

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str):
            raise InvalidAudioLocationError(
                "La localisation audio doit être une chaîne."
            )

        path = PurePosixPath(self.uri)
        if path.is_absolute() or path.as_posix() != self.uri:
            raise InvalidAudioLocationError(
                "La localisation audio doit être un chemin relatif canonique."
            )

        if len(path.parts) != 2 or ".." in path.parts:
            raise InvalidAudioLocationError(
                "La localisation audio doit cibler l'entrée d'un unique job."
            )

        job_uuid, filename = path.parts
        try:
            parsed_job_uuid = UUID(job_uuid)
        except ValueError as error:
            raise InvalidAudioLocationError(
                "La localisation audio contient un identifiant de job invalide."
            ) from error

        if str(parsed_job_uuid) != job_uuid:
            raise InvalidAudioLocationError(
                "L'identifiant du job doit utiliser sa représentation UUID canonique."
            )

        prefix = "input."
        extension = filename.removeprefix(prefix)
        if (
            not filename.startswith(prefix)
            or not 1 <= len(extension) <= 16
            or not extension.isascii()
            or not extension.isalnum()
            or extension != extension.lower()
        ):
            raise InvalidAudioLocationError(
                "La localisation audio doit respecter le format input.<extension>."
            )


@dataclass(frozen=True, slots=True)
class TranscriptionDirectory:
    """Métadonnées sûres d'un dossier de transcription inventorié."""

    job_uuid: UUID
    modified_at: datetime
    revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.job_uuid, UUID):
            raise InvalidAudioLocationError(
                "Le dossier de transcription doit être identifié par un UUID."
            )
        if not isinstance(self.modified_at, datetime) or (
            self.modified_at.tzinfo is None or self.modified_at.utcoffset() is None
        ):
            raise InvalidAudioLocationError(
                "La date de modification du dossier doit contenir un fuseau horaire."
            )
        if not isinstance(self.revision, str) or not self.revision:
            raise InvalidAudioLocationError(
                "Le dossier de transcription doit contenir une révision opaque."
            )


@dataclass(frozen=True, slots=True)
class StorageScanResult:
    """Résultat agrégé d'un inventaire sans exposer de chemin arbitraire."""

    directories: tuple[TranscriptionDirectory, ...]
    invalid_entry_count: int = 0
    unsafe_entry_count: int = 0
    error_count: int = 0
