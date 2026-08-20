from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
from shutil import copyfileobj
import subprocess
from tempfile import TemporaryDirectory
from typing import BinaryIO

from api.Media.models import AudioFormat, AudioMetadata
from api.exceptions import (
    InvalidAudioFileError,
    MediaProbeUnavailableError,
    UnsupportedAudioCodecError,
    UnsupportedAudioFormatError,
)


_COPY_CHUNK_SIZE_BYTES = 1024 * 1024

_FORMAT_ALIASES: dict[AudioFormat, frozenset[str]] = {
    AudioFormat.WAV: frozenset({"wav"}),
    AudioFormat.MP3: frozenset({"mp3"}),
    AudioFormat.OGG: frozenset({"ogg"}),
    AudioFormat.M4A: frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}),
}

_SUPPORTED_CODECS: dict[AudioFormat, frozenset[str]] = {
    AudioFormat.WAV: frozenset({"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}),
    AudioFormat.MP3: frozenset({"mp3"}),
    AudioFormat.OGG: frozenset({"vorbis", "opus"}),
    AudioFormat.M4A: frozenset({"aac", "alac"}),
}

_ABSENT_DURATION_VALUES = frozenset({"", "n/a"})


def parse_ffprobe_output(output: bytes | str) -> AudioMetadata:
    """Transforme la sortie JSON minimale de ffprobe en métadonnées validées."""
    try:
        payload = json.loads(output)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MediaProbeUnavailableError(
            "ffprobe a retourné des métadonnées inexploitables."
        ) from error

    if not isinstance(payload, Mapping):
        raise MediaProbeUnavailableError(
            "ffprobe a retourné des métadonnées inexploitables."
        )

    audio_stream = _first_audio_stream(payload.get("streams"))
    format_metadata = payload.get("format")
    if not isinstance(format_metadata, Mapping):
        raise InvalidAudioFileError(
            "Le conteneur du fichier audio ne peut pas être identifié."
        )

    audio_format = _parse_audio_format(format_metadata.get("format_name"))
    codec = _parse_audio_codec(audio_stream.get("codec_name"), audio_format)
    duration_seconds = _parse_duration(
        stream_duration=audio_stream.get("duration"),
        format_duration=format_metadata.get("duration"),
    )

    return AudioMetadata(
        duration_seconds=duration_seconds,
        format=audio_format,
        codec=codec,
    )


class FFprobeMediaProbe:
    """Inspecte un flux via ffprobe sans charger le média complet en mémoire."""

    def __init__(
        self,
        *,
        ffprobe_path: str = "ffprobe",
        timeout_seconds: float = 30.0,
    ) -> None:
        normalized_path = ffprobe_path.strip()
        if not normalized_path:
            raise ValueError("ffprobe_path ne peut pas être vide.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds doit être strictement positif.")

        self._ffprobe_path = normalized_path
        self._timeout_seconds = timeout_seconds

    def probe(self, source: BinaryIO) -> AudioMetadata:
        """Copie le flux par blocs vers un fichier seekable puis lance ffprobe.

        Le fichier temporaire n'utilise volontairement pas l'extension fournie
        par le client : ffprobe doit identifier le conteneur depuis son contenu.
        Le flux source appartient à l'appelant et n'est donc pas fermé ici.
        """
        try:
            with TemporaryDirectory(prefix="transcribe-ai-ffprobe-") as directory:
                media_path = Path(directory) / "input.media"
                with media_path.open("wb") as destination:
                    copyfileobj(
                        source,
                        destination,
                        length=_COPY_CHUNK_SIZE_BYTES,
                    )

                completed = self._run_ffprobe(media_path)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MediaProbeUnavailableError(
                "Le service d'inspection audio est indisponible."
            ) from error

        if completed.returncode != 0:
            if completed.returncode < 0:
                raise MediaProbeUnavailableError(
                    "Le processus d'inspection audio a été interrompu."
                )
            raise InvalidAudioFileError(
                "Le contenu du fichier audio ne peut pas être analysé."
            )

        return parse_ffprobe_output(completed.stdout)

    def _run_ffprobe(self, media_path: Path) -> subprocess.CompletedProcess[bytes]:
        """Exécute ffprobe avec une sortie bornée aux champs utiles au use case."""
        return subprocess.run(
            [
                self._ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                ("stream=codec_type,codec_name,duration:format=format_name,duration"),
                "-of",
                "json",
                str(media_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=self._timeout_seconds,
        )


def _first_audio_stream(streams: object) -> Mapping[str, object]:
    if isinstance(streams, list):
        for stream in streams:
            if isinstance(stream, Mapping) and stream.get("codec_type") == "audio":
                return stream

    raise InvalidAudioFileError("Le fichier ne contient aucune piste audio.")


def _parse_audio_format(raw_format_name: object) -> AudioFormat:
    if not isinstance(raw_format_name, str) or not raw_format_name.strip():
        raise InvalidAudioFileError(
            "Le conteneur du fichier audio ne peut pas être identifié."
        )

    detected_aliases = frozenset(
        alias.strip().lower() for alias in raw_format_name.split(",") if alias.strip()
    )
    for audio_format, aliases in _FORMAT_ALIASES.items():
        if detected_aliases.intersection(aliases):
            return audio_format

    raise UnsupportedAudioFormatError(
        "Le conteneur audio détecté n'est pas pris en charge."
    )


def _parse_audio_codec(raw_codec_name: object, audio_format: AudioFormat) -> str:
    if not isinstance(raw_codec_name, str) or not raw_codec_name.strip():
        raise InvalidAudioFileError("Le codec audio ne peut pas être identifié.")

    codec = raw_codec_name.strip().lower()
    if codec not in _SUPPORTED_CODECS[audio_format]:
        raise UnsupportedAudioCodecError(
            "Le codec audio détecté n'est pas pris en charge."
        )
    return codec


def _parse_duration(
    *,
    stream_duration: object,
    format_duration: object,
) -> Decimal:
    raw_duration = stream_duration
    if _duration_is_absent(raw_duration):
        raw_duration = format_duration
    if _duration_is_absent(raw_duration):
        raise InvalidAudioFileError("La durée du fichier audio est absente.")

    try:
        duration = Decimal(str(raw_duration))
    except (InvalidOperation, ValueError) as error:
        raise InvalidAudioFileError(
            "La durée du fichier audio est invalide."
        ) from error

    if not duration.is_finite() or duration <= 0:
        raise InvalidAudioFileError("La durée du fichier audio est invalide.")
    return duration


def _duration_is_absent(value: object) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in _ABSENT_DURATION_VALUES
    )
