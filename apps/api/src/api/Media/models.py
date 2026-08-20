from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class AudioFormat(StrEnum):
    """Conteneurs audio acceptés par le use case de transcription."""

    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    M4A = "m4a"


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    """Métadonnées internes extraites du contenu réel par ffprobe."""

    duration_seconds: Decimal
    format: AudioFormat
    codec: str
