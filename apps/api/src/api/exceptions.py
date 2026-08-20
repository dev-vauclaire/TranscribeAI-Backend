from __future__ import annotations

from decimal import Decimal

from transcribe_ai_shared import JobType


class TranscriptionCreationError(Exception):
    """Base des erreurs applicatives du use case de création."""


class UploadMetadataError(TranscriptionCreationError):
    """Les métadonnées déclarées de l'upload HTTP sont invalides."""


class MissingUploadFilenameError(UploadMetadataError):
    """Le client n'a pas fourni de nom de fichier exploitable."""


class EmptyUploadError(UploadMetadataError):
    """Le fichier envoyé ne contient aucun octet."""


class UploadSizeUnavailableError(UploadMetadataError):
    """La taille du fichier multipart ne peut pas être déterminée."""


class UploadTooLargeError(UploadMetadataError):
    """Le fichier annoncé dépasse la limite HTTP configurée."""

    def __init__(self, *, actual_size_bytes: int, max_size_bytes: int) -> None:
        self.actual_size_bytes = actual_size_bytes
        self.max_size_bytes = max_size_bytes
        super().__init__(
            f"Le fichier audio dépasse la limite de {max_size_bytes} octets."
        )


class UnsupportedFileExtensionError(UploadMetadataError):
    """L'extension déclarée n'est pas acceptée par l'API."""


class UnsupportedDeclaredMediaTypeError(UploadMetadataError):
    """Le type MIME déclaré n'est pas accepté pour cette extension."""


class StoredMediaValidationError(TranscriptionCreationError):
    """Le média stocké ne satisfait pas le contrat audio du use case."""


class InvalidAudioFileError(StoredMediaValidationError):
    """Le contenu stocké n'est pas un fichier audio exploitable."""


class UnsupportedAudioFormatError(StoredMediaValidationError):
    """Le conteneur audio réel n'est pas pris en charge."""


class UnsupportedAudioCodecError(StoredMediaValidationError):
    """Le codec audio réel n'est pas pris en charge."""


class AudioTooLongError(StoredMediaValidationError):
    """La durée réelle dépasse la limite du type de transcription."""

    def __init__(
        self,
        *,
        actual_duration_seconds: Decimal,
        max_duration_seconds: Decimal,
        job_type: JobType,
    ) -> None:
        self.actual_duration_seconds = actual_duration_seconds
        self.max_duration_seconds = max_duration_seconds
        self.job_type = job_type
        super().__init__(
            "La durée audio dépasse la limite "
            f"de {max_duration_seconds} secondes pour un job {job_type.value}."
        )


class MediaProbeUnavailableError(TranscriptionCreationError):
    """Le processus d'inspection média est momentanément indisponible."""


class AudioStorageUnavailableError(TranscriptionCreationError):
    """Le stockage audio est momentanément indisponible."""


class TranscriptionPersistenceError(TranscriptionCreationError):
    """Le job n'a pas pu être persisté dans PostgreSQL."""


class TranscriptionNotFoundError(Exception):
    """Le job demandé n'existe pas dans PostgreSQL."""


class TranscriptionQueryError(Exception):
    """Le statut ou le résultat n'a pas pu être consulté de façon fiable."""
