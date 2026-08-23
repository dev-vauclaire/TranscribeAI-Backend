from collections.abc import Mapping
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
    PermanentTranscriptionError,
    RetryableTranscriptionError,
    TranscriptionOutput,
)
from worker_long_form_diarization.transcribers.whisperx import (
    TRANSCRIPTION_LANGUAGE,
    WHISPERX_ALIGNMENT_ERROR_CODE,
    WHISPERX_AUDIO_DECODE_ERROR_CODE,
    WHISPERX_DIARIZATION_ERROR_CODE,
    WHISPERX_INFERENCE_ERROR_CODE,
    WHISPERX_INVALID_OUTPUT_ERROR_CODE,
    WHISPERX_SPEAKER_ASSIGNMENT_ERROR_CODE,
    WhisperXDiarizationTranscriber,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

AUDIO_CONTENT = b"audio handled by the mocked WhisperX pipeline"
AUDIO_SAMPLES = object()
ALIGNMENT_MODEL = object()
ALIGNMENT_METADATA = object()
DIARIZATION_RESULT = object()


class RecordingPipeline:
    def __init__(
        self,
        *,
        asr_result: Mapping[str, object] | None = None,
        aligned_result: Mapping[str, object] | None = None,
        assigned_result: Mapping[str, object] | None = None,
        failure_stage: str | None = None,
    ) -> None:
        self.asr_result = (
            asr_result
            if asr_result is not None
            else {"segments": [{"start": 0.0, "end": 1.0, "text": " Bonjour"}]}
        )
        self.aligned_result = (
            aligned_result
            if aligned_result is not None
            else {"segments": [{"start": 0.0, "end": 1.0, "text": " Bonjour"}]}
        )
        self.assigned_result = (
            assigned_result
            if assigned_result is not None
            else {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "text": " Bonjour",
                        "speaker": "SPEAKER_00",
                    }
                ]
            }
        )
        self.failure_stage = failure_stage
        self.failure = RuntimeError(
            "model failed with token=secret and path=/private/input.wav"
        )
        self.calls: list[str] = []
        self.thread_ids: list[int] = []
        self.temporary_paths: list[Path] = []
        self.materialized_content: bytes | None = None
        self.asr_call: tuple[object, int, str] | None = None
        self.align_call: (
            tuple[
                list[Mapping[str, object]],
                object,
                object,
                object,
                str,
                bool,
            ]
            | None
        ) = None
        self.diarization_audio: object | None = None
        self.assignment_call: tuple[object, Mapping[str, object]] | None = None

    def load_audio(self, audio_file: str) -> object:
        self._record("decode")
        path = Path(audio_file)
        self.temporary_paths.append(path)
        self.materialized_content = path.read_bytes()
        self._raise_if_requested("decode")
        return AUDIO_SAMPLES

    def transcribe(
        self,
        audio: object,
        *,
        batch_size: int,
        language: str,
    ) -> Mapping[str, object]:
        self._record("asr")
        self.asr_call = (audio, batch_size, language)
        self._raise_if_requested("asr")
        return self.asr_result

    def align(
        self,
        transcript_segments: list[Mapping[str, object]],
        model: object,
        model_metadata: object,
        audio: object,
        device: str,
        *,
        return_char_alignments: bool,
    ) -> Mapping[str, object]:
        self._record("alignment")
        self.align_call = (
            transcript_segments,
            model,
            model_metadata,
            audio,
            device,
            return_char_alignments,
        )
        self._raise_if_requested("alignment")
        return self.aligned_result

    def diarize(self, audio: object) -> object:
        self._record("diarization")
        self.diarization_audio = audio
        self._raise_if_requested("diarization")
        return DIARIZATION_RESULT

    def assign_speakers(
        self,
        diarization: object,
        transcript: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._record("speaker_assignment")
        self.assignment_call = (diarization, transcript)
        self._raise_if_requested("speaker_assignment")
        return self.assigned_result

    def _record(self, stage: str) -> None:
        self.calls.append(stage)
        self.thread_ids.append(get_ident())

    def _raise_if_requested(self, stage: str) -> None:
        if self.failure_stage == stage:
            raise self.failure


def _make_transcriber(
    tmp_path: Path,
    pipeline: RecordingPipeline,
    *,
    extension: str = "m4a",
    batch_size: int = 8,
) -> tuple[WhisperXDiarizationTranscriber, AudioLocation]:
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    location = storage.save(
        uuid4(),
        BytesIO(AUDIO_CONTENT),
        extension=extension,
    )
    return (
        WhisperXDiarizationTranscriber(
            storage=storage,
            load_audio=pipeline.load_audio,
            asr_model=pipeline,
            align=pipeline.align,
            alignment_model=ALIGNMENT_MODEL,
            alignment_metadata=ALIGNMENT_METADATA,
            diarization_pipeline=pipeline.diarize,
            assign_word_speakers=pipeline.assign_speakers,
            device="cuda",
            batch_size=batch_size,
        ),
        location,
    )


async def test_transcribe_runs_the_complete_pipeline_and_maps_output(
    tmp_path: Path,
) -> None:
    asr_segments = [
        {"start": 0.125, "end": 1.25, "text": " Bonjour"},
        {"start": 1.25, "end": 3.875, "text": " Madame."},
    ]
    aligned_result = {"segments": [dict(segment) for segment in asr_segments]}
    assigned_result = {
        "segments": [
            {**asr_segments[0], "speaker": "SPEAKER_00"},
            {**asr_segments[1], "speaker": "SPEAKER_01"},
        ]
    }
    pipeline = RecordingPipeline(
        asr_result={"segments": asr_segments},
        aligned_result=aligned_result,
        assigned_result=assigned_result,
    )
    transcriber, location = _make_transcriber(tmp_path, pipeline, batch_size=12)

    output = await transcriber.transcribe(location)

    assert output == TranscriptionOutput(
        result={
            "text": "Bonjour Madame.",
            "language": "fr",
            "speaker_count": 2,
            "segments": [
                {
                    "start": 0.125,
                    "end": 1.25,
                    "text": "Bonjour",
                    "speaker": "SPEAKER_00",
                },
                {
                    "start": 1.25,
                    "end": 3.875,
                    "text": "Madame.",
                    "speaker": "SPEAKER_01",
                },
            ],
        },
        speaker_count=2,
    )
    assert pipeline.calls == [
        "decode",
        "asr",
        "alignment",
        "diarization",
        "speaker_assignment",
    ]
    assert pipeline.materialized_content == AUDIO_CONTENT
    assert pipeline.asr_call == (AUDIO_SAMPLES, 12, TRANSCRIPTION_LANGUAGE)
    assert pipeline.align_call == (
        asr_segments,
        ALIGNMENT_MODEL,
        ALIGNMENT_METADATA,
        AUDIO_SAMPLES,
        "cuda",
        False,
    )
    assert pipeline.diarization_audio is AUDIO_SAMPLES
    assert pipeline.assignment_call == (DIARIZATION_RESULT, aligned_result)


async def test_transcribe_materializes_and_cleans_the_temporary_audio_off_loop(
    tmp_path: Path,
) -> None:
    event_loop_thread_id = get_ident()
    pipeline = RecordingPipeline()
    transcriber, location = _make_transcriber(tmp_path, pipeline, extension="ogg")

    await transcriber.transcribe(location)

    assert len(pipeline.temporary_paths) == 1
    assert pipeline.temporary_paths[0].suffix == ".ogg"
    assert not pipeline.temporary_paths[0].exists()
    assert set(pipeline.thread_ids).isdisjoint({event_loop_thread_id})
    assert len(set(pipeline.thread_ids)) == 1


async def test_empty_asr_result_skips_alignment_and_diarization(
    tmp_path: Path,
) -> None:
    pipeline = RecordingPipeline(asr_result={"segments": []})
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    output = await transcriber.transcribe(location)

    assert output == TranscriptionOutput(
        result={
            "text": "",
            "language": "fr",
            "speaker_count": 0,
            "segments": [],
        },
        speaker_count=0,
    )
    assert pipeline.calls == ["decode", "asr"]


async def test_empty_aligned_result_skips_diarization(
    tmp_path: Path,
) -> None:
    pipeline = RecordingPipeline(
        aligned_result={"segments": []},
        assigned_result={"segments": []},
    )
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    output = await transcriber.transcribe(location)

    assert output.result == {
        "text": "",
        "language": "fr",
        "speaker_count": 0,
        "segments": [],
    }
    assert pipeline.calls == ["decode", "asr", "alignment"]


@pytest.mark.parametrize(
    ("speakers", "expected_speakers", "expected_texts", "speaker_count"),
    [
        (
            ["SPEAKER_00", "SPEAKER_00"],
            ["SPEAKER_00"],
            ["message 0 message 1"],
            1,
        ),
        (
            ["SPEAKER_00", None, "SPEAKER_00"],
            ["SPEAKER_00", None, "SPEAKER_00"],
            ["message 0", "message 1", "message 2"],
            1,
        ),
        (
            [None, None],
            [None, None],
            ["message 0", "message 1"],
            0,
        ),
        (
            ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"],
            ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"],
            ["message 0", "message 1", "message 2"],
            2,
        ),
    ],
)
async def test_transcribe_only_merges_adjacent_segments_from_a_known_speaker(
    tmp_path: Path,
    speakers: list[str | None],
    expected_speakers: list[str | None],
    expected_texts: list[str],
    speaker_count: int,
) -> None:
    assigned_segments = [
        {
            "start": float(index),
            "end": float(index + 1),
            "text": f" message {index} ",
            "speaker": speaker,
        }
        for index, speaker in enumerate(speakers)
    ]
    pipeline = RecordingPipeline(assigned_result={"segments": assigned_segments})
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    output = await transcriber.transcribe(location)

    segments = cast(list[dict[str, object]], output.result["segments"])
    assert [segment["speaker"] for segment in segments] == expected_speakers
    assert [segment["text"] for segment in segments] == expected_texts
    assert output.result["speaker_count"] == speaker_count
    assert output.speaker_count == speaker_count


async def test_merged_segment_keeps_first_start_and_largest_end(
    tmp_path: Path,
) -> None:
    pipeline = RecordingPipeline(
        assigned_result={
            "segments": [
                {
                    "start": 0.0,
                    "end": 4.0,
                    "text": " première",
                    "speaker": "SPEAKER_00",
                },
                {
                    "start": 1.0,
                    "end": 2.0,
                    "text": " seconde",
                    "speaker": "SPEAKER_00",
                },
            ]
        }
    )
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    output = await transcriber.transcribe(location)

    assert output.result["segments"] == [
        {
            "start": 0.0,
            "end": 4.0,
            "text": "première seconde",
            "speaker": "SPEAKER_00",
        }
    ]


async def test_segment_without_speaker_is_preserved_as_unattributed(
    tmp_path: Path,
) -> None:
    pipeline = RecordingPipeline(
        assigned_result={
            "segments": [
                {"start": 0.0, "end": 1.0, "text": " Sans speaker"},
                {
                    "start": 1.0,
                    "end": 2.0,
                    "text": " Speaker vide",
                    "speaker": "  ",
                },
            ]
        }
    )
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    output = await transcriber.transcribe(location)

    segments = cast(list[dict[str, object]], output.result["segments"])
    assert [segment["speaker"] for segment in segments] == [None, None]
    assert output.result["speaker_count"] == 0


@pytest.mark.parametrize(
    ("failure_stage", "error_type", "error_code"),
    [
        (
            "decode",
            PermanentTranscriptionError,
            WHISPERX_AUDIO_DECODE_ERROR_CODE,
        ),
        ("asr", RetryableTranscriptionError, WHISPERX_INFERENCE_ERROR_CODE),
        (
            "alignment",
            RetryableTranscriptionError,
            WHISPERX_ALIGNMENT_ERROR_CODE,
        ),
        (
            "diarization",
            RetryableTranscriptionError,
            WHISPERX_DIARIZATION_ERROR_CODE,
        ),
        (
            "speaker_assignment",
            PermanentTranscriptionError,
            WHISPERX_SPEAKER_ASSIGNMENT_ERROR_CODE,
        ),
    ],
)
async def test_transcribe_classifies_pipeline_errors_without_sensitive_details(
    tmp_path: Path,
    failure_stage: str,
    error_type: type[PermanentTranscriptionError | RetryableTranscriptionError],
    error_code: str,
) -> None:
    pipeline = RecordingPipeline(failure_stage=failure_stage)
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    with pytest.raises(error_type) as raised:
        await transcriber.transcribe(location)

    assert raised.value.error_code == error_code
    assert raised.value.__cause__ is pipeline.failure
    assert "secret" not in str(raised.value)
    assert "/private/input.wav" not in str(raised.value)
    assert pipeline.temporary_paths
    assert not pipeline.temporary_paths[0].exists()


@pytest.mark.parametrize(
    ("segments", "case_name"),
    [
        ([{"end": 1.0, "text": "start absent"}], "missing start"),
        (
            [{"start": "0", "end": 1.0, "text": "timestamp string"}],
            "timestamp string",
        ),
        (
            [{"start": True, "end": 1.0, "text": "timestamp bool"}],
            "timestamp bool",
        ),
        (
            [{"start": float("inf"), "end": 1.0, "text": "timestamp inf"}],
            "infinite timestamp",
        ),
        (
            [{"start": -0.1, "end": 1.0, "text": "negative start"}],
            "negative timestamp",
        ),
        (
            [{"start": 2.0, "end": 1.0, "text": "negative duration"}],
            "end before start",
        ),
        (
            [
                {"start": 2.0, "end": 3.0, "text": "second"},
                {"start": 1.0, "end": 2.0, "text": "first"},
            ],
            "out of order",
        ),
        ([{"start": 0.0, "end": 1.0, "text": None}], "invalid text"),
        (
            [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "speaker invalide",
                    "speaker": 42,
                }
            ],
            "invalid speaker",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
async def test_transcribe_rejects_malformed_segments_as_permanent_errors(
    tmp_path: Path,
    segments: list[dict[str, object]],
    case_name: str,
) -> None:
    pipeline = RecordingPipeline(assigned_result={"segments": segments})
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    with pytest.raises(PermanentTranscriptionError) as raised:
        await transcriber.transcribe(location)

    assert raised.value.error_code == WHISPERX_INVALID_OUTPUT_ERROR_CODE
    assert case_name


@pytest.mark.parametrize(
    ("stage", "malformed_result", "expected_calls"),
    [
        ("asr", {}, ["decode", "asr"]),
        ("alignment", {}, ["decode", "asr", "alignment"]),
        (
            "speaker_assignment",
            {},
            [
                "decode",
                "asr",
                "alignment",
                "diarization",
                "speaker_assignment",
            ],
        ),
    ],
)
async def test_transcribe_rejects_incomplete_stage_payloads(
    tmp_path: Path,
    stage: str,
    malformed_result: Mapping[str, object],
    expected_calls: list[str],
) -> None:
    result_argument_for_stage = {
        "asr": "asr_result",
        "alignment": "aligned_result",
        "speaker_assignment": "assigned_result",
    }
    pipeline_arguments: dict[str, Mapping[str, object]] = {
        result_argument_for_stage[stage]: malformed_result
    }
    pipeline = RecordingPipeline(**pipeline_arguments)
    transcriber, location = _make_transcriber(tmp_path, pipeline)

    with pytest.raises(PermanentTranscriptionError) as raised:
        await transcriber.transcribe(location)

    assert raised.value.error_code == WHISPERX_INVALID_OUTPUT_ERROR_CODE
    assert pipeline.calls == expected_calls


async def test_transcribe_preserves_storage_errors(tmp_path: Path) -> None:
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    pipeline = RecordingPipeline()
    transcriber = WhisperXDiarizationTranscriber(
        storage=storage,
        load_audio=pipeline.load_audio,
        asr_model=pipeline,
        align=pipeline.align,
        alignment_model=ALIGNMENT_MODEL,
        alignment_metadata=ALIGNMENT_METADATA,
        diarization_pipeline=pipeline.diarize,
        assign_word_speakers=pipeline.assign_speakers,
        device="cpu",
        batch_size=1,
    )

    with pytest.raises(AudioNotFoundError):
        await transcriber.transcribe(AudioLocation(f"{uuid4()}/input.wav"))

    assert pipeline.calls == []


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
async def test_transcriber_rejects_invalid_batch_size(batch_size: object) -> None:
    pipeline = RecordingPipeline()

    with pytest.raises(ValueError, match="batch_size"):
        WhisperXDiarizationTranscriber(
            storage=cast(FileSystemAudioStorage, object()),
            load_audio=pipeline.load_audio,
            asr_model=pipeline,
            align=pipeline.align,
            alignment_model=ALIGNMENT_MODEL,
            alignment_metadata=ALIGNMENT_METADATA,
            diarization_pipeline=pipeline.diarize,
            assign_word_speakers=pipeline.assign_speakers,
            device="cpu",
            batch_size=cast(int, batch_size),
        )
