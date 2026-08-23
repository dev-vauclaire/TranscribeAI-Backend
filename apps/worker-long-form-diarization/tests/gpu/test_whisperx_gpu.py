import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from transcribe_ai_shared import FileSystemAudioStorage, TranscriptionOutput
from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings
from worker_long_form_diarization.transcribers.whisperx_factory import (
    create_whisperx_transcriber,
)


pytestmark = [
    pytest.mark.gpu,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_GPU_TESTS") != "1",
        reason="Les tests ML nécessitent RUN_GPU_TESTS=1",
    ),
]


async def test_whisperx_transcribes_and_diarizes_a_real_french_audio_on_gpu(
    tmp_path: Path,
    french_dialogue_audio_path: Path,
) -> None:
    hugging_face_token = os.getenv("WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN")
    if hugging_face_token is None:
        pytest.fail("WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN est requis")

    model_directory = tmp_path / "models"
    model_directory.mkdir()
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    with french_dialogue_audio_path.open("rb") as audio:
        location = storage.save(
            uuid4(),
            audio,
            extension=french_dialogue_audio_path.suffix.removeprefix(".").lower(),
        )

    settings = WorkerLongFormDiarizationSettings(
        worker_id="worker-long-form-diarization-gpu-test",
        worker_transcriber_hugging_face_token=hugging_face_token,
        worker_transcriber_model_directory=model_directory,
        worker_transcriber_device="cuda",
        worker_transcriber_compute_type="default",
    )
    transcriber = create_whisperx_transcriber(settings=settings, storage=storage)

    output = await transcriber.transcribe(location)

    assert isinstance(output, TranscriptionOutput)
    assert output.result["language"] == "fr"
    assert isinstance(output.result["text"], str)
    assert output.result["text"]
    assert output.speaker_count == output.result["speaker_count"]

    segments = cast(list[dict[str, object]], output.result["segments"])
    assert segments
    speakers: set[str] = set()
    for segment in segments:
        start = segment["start"]
        end = segment["end"]
        assert isinstance(start, int | float)
        assert isinstance(end, int | float)
        assert start < end
        speaker = segment["speaker"]
        assert speaker is None or isinstance(speaker, str)
        if speaker is not None:
            speakers.add(speaker)

    assert speakers
    assert output.speaker_count == len(speakers)
    for previous, current in zip(segments, segments[1:], strict=False):
        previous_speaker = previous["speaker"]
        assert previous_speaker is None or previous_speaker != current["speaker"]
