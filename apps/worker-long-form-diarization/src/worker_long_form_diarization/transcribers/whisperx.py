import asyncio
import math
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Protocol

from transcribe_ai_shared import (
    AudioLocation,
    AudioStorage,
    PermanentTranscriptionError,
    RetryableTranscriptionError,
    TranscriptionOutput,
)


TRANSCRIPTION_LANGUAGE = "fr"
WHISPERX_AUDIO_DECODE_ERROR_CODE = "WHISPERX_AUDIO_DECODE_FAILED"
WHISPERX_INFERENCE_ERROR_CODE = "WHISPERX_INFERENCE_FAILED"
WHISPERX_ALIGNMENT_ERROR_CODE = "WHISPERX_ALIGNMENT_FAILED"
WHISPERX_DIARIZATION_ERROR_CODE = "WHISPERX_DIARIZATION_FAILED"
WHISPERX_SPEAKER_ASSIGNMENT_ERROR_CODE = "WHISPERX_SPEAKER_ASSIGNMENT_FAILED"
WHISPERX_INVALID_OUTPUT_ERROR_CODE = "WHISPERX_INVALID_OUTPUT"

_COPY_BUFFER_SIZE = 1024 * 1024


class _AudioLoader(Protocol):
    def __call__(self, audio_file: str) -> object: ...


class _ASRModel(Protocol):
    def transcribe(
        self,
        audio: object,
        *,
        batch_size: int,
        language: str,
    ) -> Mapping[str, object]: ...


class _AlignmentFunction(Protocol):
    def __call__(
        self,
        transcript_segments: list[Mapping[str, object]],
        model: object,
        model_metadata: object,
        audio: object,
        device: str,
        *,
        return_char_alignments: bool,
    ) -> Mapping[str, object]: ...


class _DiarizationPipeline(Protocol):
    def __call__(self, audio: object) -> object: ...


class _SpeakerAssignmentFunction(Protocol):
    def __call__(
        self,
        diarization: object,
        transcript: Mapping[str, object],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class _StandardizedSegment:
    start: float
    end: float
    text: str
    speaker: str | None


class WhisperXDiarizationTranscriber:
    """Adapte le pipeline WhisperX/Pyannote au contrat commun des workers."""

    def __init__(
        self,
        *,
        storage: AudioStorage,
        load_audio: _AudioLoader,
        asr_model: _ASRModel,
        align: _AlignmentFunction,
        alignment_model: object,
        alignment_metadata: object,
        diarization_pipeline: _DiarizationPipeline,
        assign_word_speakers: _SpeakerAssignmentFunction,
        device: str,
        batch_size: int,
    ) -> None:
        if not isinstance(device, str) or not device.strip():
            raise ValueError("device doit être une chaîne non vide")
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size doit être un entier strictement positif")

        self._storage = storage
        self._load_audio = load_audio
        self._asr_model = asr_model
        self._align = align
        self._alignment_model = alignment_model
        self._alignment_metadata = alignment_metadata
        self._diarization_pipeline = diarization_pipeline
        self._assign_word_speakers = assign_word_speakers
        self._device = device
        self._batch_size = batch_size

    async def transcribe(
        self,
        audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        """Exécute tout le pipeline bloquant hors de la boucle asyncio."""
        return await asyncio.to_thread(self._transcribe_sync, audio_location)

    def _transcribe_sync(
        self,
        audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        suffix = PurePosixPath(audio_location.uri).suffix

        with TemporaryDirectory(prefix="transcribe-ai-whisperx-") as directory:
            audio_file = Path(directory) / f"input{suffix}"
            self._materialize_audio(audio_location, audio_file)
            return self._run_pipeline(audio_file)

    def _materialize_audio(
        self,
        audio_location: AudioLocation,
        audio_file: Path,
    ) -> None:
        with self._storage.open(audio_location) as source:
            with audio_file.open("xb") as destination:
                shutil.copyfileobj(
                    source,
                    destination,
                    length=_COPY_BUFFER_SIZE,
                )

    def _run_pipeline(self, audio_file: Path) -> TranscriptionOutput:
        try:
            audio = self._load_audio(str(audio_file))
        except Exception as error:
            raise PermanentTranscriptionError(
                WHISPERX_AUDIO_DECODE_ERROR_CODE
            ) from error

        try:
            transcription = self._asr_model.transcribe(
                audio,
                batch_size=self._batch_size,
                language=TRANSCRIPTION_LANGUAGE,
            )
        except Exception as error:
            raise RetryableTranscriptionError(WHISPERX_INFERENCE_ERROR_CODE) from error

        transcription_segments = _extract_segments(transcription)
        if not transcription_segments:
            return _empty_output()

        try:
            aligned_transcription = self._align(
                transcription_segments,
                self._alignment_model,
                self._alignment_metadata,
                audio,
                self._device,
                return_char_alignments=False,
            )
        except Exception as error:
            raise RetryableTranscriptionError(WHISPERX_ALIGNMENT_ERROR_CODE) from error

        if not _extract_segments(aligned_transcription):
            return _empty_output()

        try:
            diarization = self._diarization_pipeline(audio)
        except Exception as error:
            raise RetryableTranscriptionError(
                WHISPERX_DIARIZATION_ERROR_CODE
            ) from error

        try:
            speaker_transcription = self._assign_word_speakers(
                diarization,
                aligned_transcription,
            )
        except Exception as error:
            raise PermanentTranscriptionError(
                WHISPERX_SPEAKER_ASSIGNMENT_ERROR_CODE
            ) from error

        segments = _standardize_segments(_extract_segments(speaker_transcription))
        merged_segments = _merge_adjacent_speaker_segments(segments)
        return _build_output(merged_segments)


def _extract_segments(
    result: Mapping[str, object],
) -> list[Mapping[str, object]]:
    if not isinstance(result, Mapping):
        raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)

    raw_segments = result.get("segments")
    if not isinstance(raw_segments, list):
        raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)
    if not all(isinstance(segment, Mapping) for segment in raw_segments):
        raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)

    return list(raw_segments)


def _standardize_segments(
    raw_segments: list[Mapping[str, object]],
) -> list[_StandardizedSegment]:
    standardized: list[_StandardizedSegment] = []
    previous_start: float | None = None

    for raw_segment in raw_segments:
        start = _timestamp(raw_segment.get("start"))
        end = _timestamp(raw_segment.get("end"))
        if start < 0 or end < start:
            raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)
        if previous_start is not None and start < previous_start:
            raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)
        previous_start = start

        raw_text = raw_segment.get("text")
        if not isinstance(raw_text, str):
            raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)
        text = raw_text.strip()

        speaker = _speaker(raw_segment.get("speaker"))
        if text:
            standardized.append(
                _StandardizedSegment(
                    start=start,
                    end=end,
                    text=text,
                    speaker=speaker,
                )
            )

    return standardized


def _timestamp(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)

    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)
    return timestamp


def _speaker(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PermanentTranscriptionError(WHISPERX_INVALID_OUTPUT_ERROR_CODE)

    speaker = value.strip()
    return speaker or None


def _merge_adjacent_speaker_segments(
    segments: list[_StandardizedSegment],
) -> list[_StandardizedSegment]:
    """Fusionne un même speaker connu ; un speaker absent reste une frontière."""
    merged: list[_StandardizedSegment] = []

    for segment in segments:
        if (
            merged
            and segment.speaker is not None
            and merged[-1].speaker == segment.speaker
        ):
            previous = merged[-1]
            merged[-1] = _StandardizedSegment(
                start=previous.start,
                end=max(previous.end, segment.end),
                text=_join_text(previous.text, segment.text),
                speaker=previous.speaker,
            )
            continue
        merged.append(segment)

    return merged


def _build_output(segments: list[_StandardizedSegment]) -> TranscriptionOutput:
    mapped_segments = [
        {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "speaker": segment.speaker,
        }
        for segment in segments
    ]
    speakers = {segment.speaker for segment in segments if segment.speaker is not None}

    return TranscriptionOutput(
        result={
            "text": _join_text(*(segment.text for segment in segments)),
            "language": TRANSCRIPTION_LANGUAGE,
            "speaker_count": len(speakers),
            "segments": mapped_segments,
        },
        speaker_count=len(speakers),
    )


def _empty_output() -> TranscriptionOutput:
    return TranscriptionOutput(
        result={
            "text": "",
            "language": TRANSCRIPTION_LANGUAGE,
            "speaker_count": 0,
            "segments": [],
        },
        speaker_count=0,
    )


def _join_text(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part.strip())
