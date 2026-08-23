from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import get_ident
from typing import cast
from uuid import uuid4

import pytest

from transcribe_ai_shared import (
    AudioLocation,
    AudioNotFoundError,
    FileSystemAudioStorage,
    RetryableTranscriptionError,
    TranscriptionOutput,
)
from worker_fast.transcribers.faster_whisper import (
    FASTER_WHISPER_ERROR_CODE,
    FasterWhisperTranscriber,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

AUDIO_CONTENT = b"audio handled by the mocked engine"


@dataclass(frozen=True, slots=True)
class StubSegment:
    start: float
    end: float
    text: str


class RecordingModel:
    def __init__(
        self,
        segments: tuple[StubSegment, ...] = (),
        *,
        call_error: Exception | None = None,
        iteration_error: Exception | None = None,
    ) -> None:
        self._segments = segments
        self._call_error = call_error
        self._iteration_error = iteration_error
        self.audio_content: bytes | None = None
        self.language: str | None = None
        self.vad_filter: bool | None = None
        self.thread_id: int | None = None

    def transcribe(
        self,
        audio,
        *,
        language: str,
        vad_filter: bool,
    ):
        self.thread_id = get_ident()
        self.audio_content = audio.read()
        self.language = language
        self.vad_filter = vad_filter
        if self._call_error is not None:
            raise self._call_error

        def generate_segments():
            yield from self._segments
            if self._iteration_error is not None:
                raise self._iteration_error

        return generate_segments(), object()


def _make_transcriber(
    tmp_path: Path,
    model: RecordingModel,
) -> tuple[FasterWhisperTranscriber, AudioLocation]:
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    job_uuid = uuid4()
    location = storage.save(
        job_uuid,
        BytesIO(AUDIO_CONTENT),
        extension="wav",
    )
    return FasterWhisperTranscriber(model=model, storage=storage), location


async def test_transcribe_maps_normal_segments_to_transcription_output(
    tmp_path: Path,
) -> None:
    model = RecordingModel(
        (
            StubSegment(start=0.0, end=0.75, text=" Bonjour"),
            StubSegment(start=0.75, end=1.5, text=" tout le monde."),
        )
    )
    transcriber, location = _make_transcriber(tmp_path, model)

    output = await transcriber.transcribe(location)

    assert output == TranscriptionOutput(
        result={
            "text": "Bonjour tout le monde.",
            "language": "fr",
            "segments": [
                {"start": 0.0, "end": 0.75, "text": "Bonjour"},
                {"start": 0.75, "end": 1.5, "text": "tout le monde."},
            ],
        }
    )
    assert model.audio_content == AUDIO_CONTENT
    assert model.language == "fr"
    assert model.vad_filter is True


async def test_transcribe_preserves_segment_timestamps(tmp_path: Path) -> None:
    model = RecordingModel(
        (StubSegment(start=1.234, end=5.678, text=" Segment horodaté"),)
    )
    transcriber, location = _make_transcriber(tmp_path, model)

    output = await transcriber.transcribe(location)

    assert output.result["segments"] == [
        {"start": 1.234, "end": 5.678, "text": "Segment horodaté"}
    ]


async def test_transcribe_returns_a_standardized_empty_result(
    tmp_path: Path,
) -> None:
    transcriber, location = _make_transcriber(tmp_path, RecordingModel())

    output = await transcriber.transcribe(location)

    assert output == TranscriptionOutput(
        result={"text": "", "language": "fr", "segments": []}
    )


@pytest.mark.parametrize("failure_stage", ["call", "iteration"])
async def test_transcribe_translates_engine_errors_without_exposing_details(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    engine_error = RuntimeError("model token=secret path=/private/audio.wav")
    model = RecordingModel(
        call_error=engine_error if failure_stage == "call" else None,
        iteration_error=engine_error if failure_stage == "iteration" else None,
    )
    transcriber, location = _make_transcriber(tmp_path, model)

    with pytest.raises(RetryableTranscriptionError) as raised:
        await transcriber.transcribe(location)

    assert raised.value.error_code == FASTER_WHISPER_ERROR_CODE
    assert raised.value.__cause__ is engine_error
    assert "secret" not in str(raised.value)
    assert "/private/audio.wav" not in str(raised.value)


async def test_transcribe_preserves_storage_errors(tmp_path: Path) -> None:
    model = RecordingModel()
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    missing_location = AudioLocation(f"{uuid4()}/input.wav")
    transcriber = FasterWhisperTranscriber(model=model, storage=storage)

    with pytest.raises(AudioNotFoundError):
        await transcriber.transcribe(missing_location)

    assert model.thread_id is None


async def test_transcribe_preserves_invalid_segment_contract_errors(
    tmp_path: Path,
) -> None:
    malformed_segment = StubSegment(
        start=cast(float, "not-a-timestamp"),
        end=1.0,
        text=" Segment invalide",
    )
    transcriber, location = _make_transcriber(
        tmp_path,
        RecordingModel((malformed_segment,)),
    )

    with pytest.raises(ValueError, match="could not convert string to float"):
        await transcriber.transcribe(location)


async def test_transcribe_runs_the_engine_outside_the_event_loop_thread(
    tmp_path: Path,
) -> None:
    event_loop_thread_id = get_ident()
    model = RecordingModel()
    transcriber, location = _make_transcriber(tmp_path, model)

    await transcriber.transcribe(location)

    assert model.thread_id is not None
    assert model.thread_id != event_loop_thread_id
