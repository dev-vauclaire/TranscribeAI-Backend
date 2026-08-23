import asyncio
from collections.abc import Iterable
from typing import BinaryIO, Protocol

from transcribe_ai_shared import (
    AudioLocation,
    AudioStorage,
    RetryableTranscriptionError,
    TranscriptionOutput,
)


FASTER_WHISPER_ERROR_CODE = "FASTER_WHISPER_INFERENCE_FAILED"
TRANSCRIPTION_LANGUAGE = "fr"


class _FasterWhisperSegment(Protocol):
    start: float
    end: float
    text: str


class _FasterWhisperModel(Protocol):
    def transcribe(
        self,
        audio: BinaryIO,
        *,
        language: str,
        vad_filter: bool,
    ) -> tuple[Iterable[_FasterWhisperSegment], object]: ...


class FasterWhisperTranscriber:
    """Adapte Faster-Whisper au contrat moteur indépendant du runtime worker."""

    def __init__(
        self,
        *,
        model: _FasterWhisperModel,
        storage: AudioStorage,
    ) -> None:
        self._model = model
        self._storage = storage

    async def transcribe(
        self,
        audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        """Exécute toute l'inférence synchrone hors de la boucle asyncio."""
        return await asyncio.to_thread(self._transcribe_sync, audio_location)

    def _transcribe_sync(
        self,
        audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        """Consomme aussi le générateur lazy pendant que le flux reste ouvert."""
        with self._storage.open(audio_location) as audio:
            try:
                transcription = self._model.transcribe(
                    audio,
                    language=TRANSCRIPTION_LANGUAGE,
                    vad_filter=True,
                )
            except Exception as error:
                raise RetryableTranscriptionError(FASTER_WHISPER_ERROR_CODE) from error

            segments, _ = transcription
            raw_text_parts: list[str] = []
            mapped_segments: list[dict[str, float | str]] = []
            segment_iterator = iter(segments)
            while True:
                try:
                    segment = next(segment_iterator)
                except StopIteration:
                    break
                except Exception as error:
                    raise RetryableTranscriptionError(
                        FASTER_WHISPER_ERROR_CODE
                    ) from error

                raw_text_parts.append(segment.text)
                mapped_segments.append(
                    {
                        "start": float(segment.start),
                        "end": float(segment.end),
                        "text": segment.text.strip(),
                    }
                )

        return TranscriptionOutput(
            result={
                "text": "".join(raw_text_parts).strip(),
                "language": TRANSCRIPTION_LANGUAGE,
                "segments": mapped_segments,
            }
        )
