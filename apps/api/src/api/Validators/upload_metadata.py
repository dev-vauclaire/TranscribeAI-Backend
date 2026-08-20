from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import UploadFile

from api.exceptions import (
    EmptyUploadError,
    MissingUploadFilenameError,
    UnsupportedDeclaredMediaTypeError,
    UnsupportedFileExtensionError,
    UploadSizeUnavailableError,
    UploadTooLargeError,
)


_DECLARED_CONTENT_TYPES_BY_EXTENSION: dict[str, frozenset[str]] = {
    "wav": frozenset(
        {
            "audio/wav",
            "audio/wave",
            "audio/x-wav",
            "audio/vnd.wave",
        }
    ),
    "mp3": frozenset({"audio/mpeg", "audio/mp3"}),
    "ogg": frozenset({"audio/ogg", "application/ogg"}),
    "m4a": frozenset({"audio/mp4", "audio/m4a", "audio/x-m4a"}),
}


@dataclass(frozen=True, slots=True)
class ValidatedUploadMetadata:
    """Métadonnées HTTP normalisées, sans garantie sur le contenu du fichier."""

    extension: str
    size_bytes: int
    declared_content_type: str


class UploadMetadataValidator:
    """Rejette rapidement les métadonnées multipart manifestement invalides."""

    def __init__(self, max_size_bytes: int) -> None:
        if (
            not isinstance(max_size_bytes, int)
            or isinstance(max_size_bytes, bool)
            or max_size_bytes < 1
        ):
            raise ValueError("max_size_bytes doit être un entier strictement positif.")
        self._max_size_bytes = max_size_bytes

    def validate(self, upload: UploadFile) -> ValidatedUploadMetadata:
        """Valide uniquement filename, taille et MIME, sans lire le flux uploadé."""
        extension = self._extract_supported_extension(upload.filename)
        size_bytes = self._validate_size(upload.size)
        declared_content_type = self._validate_declared_content_type(
            upload.content_type,
            extension=extension,
        )
        return ValidatedUploadMetadata(
            extension=extension,
            size_bytes=size_bytes,
            declared_content_type=declared_content_type,
        )

    @staticmethod
    def _extract_supported_extension(filename: str | None) -> str:
        if filename is None or not filename.strip():
            raise MissingUploadFilenameError(
                "Le fichier audio doit posséder un nom exploitable."
            )

        # Certains clients transmettent encore un faux chemin Windows. Seul le
        # basename est inspecté et le nom fourni n'est jamais utilisé pour stocker.
        basename = PurePosixPath(filename.strip().replace("\\", "/")).name
        if not basename or basename in {".", ".."}:
            raise MissingUploadFilenameError(
                "Le fichier audio doit posséder un nom exploitable."
            )

        suffix = PurePosixPath(basename).suffix
        extension = suffix.removeprefix(".").lower()
        if extension not in _DECLARED_CONTENT_TYPES_BY_EXTENSION:
            raise UnsupportedFileExtensionError(
                "L'extension du fichier audio n'est pas prise en charge."
            )
        return extension

    def _validate_size(self, size_bytes: int | None) -> int:
        if size_bytes is None or isinstance(size_bytes, bool) or size_bytes < 0:
            raise UploadSizeUnavailableError(
                "La taille du fichier audio n'est pas exploitable."
            )
        if size_bytes == 0:
            raise EmptyUploadError("Le fichier audio est vide.")
        if size_bytes > self._max_size_bytes:
            raise UploadTooLargeError(
                actual_size_bytes=size_bytes,
                max_size_bytes=self._max_size_bytes,
            )
        return size_bytes

    @staticmethod
    def _validate_declared_content_type(
        content_type: str | None,
        *,
        extension: str,
    ) -> str:
        normalized_content_type = (content_type or "").partition(";")[0].strip().lower()
        if (
            normalized_content_type
            not in _DECLARED_CONTENT_TYPES_BY_EXTENSION[extension]
        ):
            raise UnsupportedDeclaredMediaTypeError(
                "Le type MIME déclaré ne correspond pas au format du fichier audio."
            )
        return normalized_content_type
