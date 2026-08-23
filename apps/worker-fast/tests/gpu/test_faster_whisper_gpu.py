import os
from pathlib import Path
from uuid import uuid4

import pytest

from transcribe_ai_shared import FileSystemAudioStorage, TranscriptionOutput
from worker_fast.transcribers import FasterWhisperTranscriber


pytestmark = [
    pytest.mark.gpu,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_GPU_TESTS") != "1",
        reason="Les tests ML nécessitent RUN_GPU_TESTS=1",
    ),
]

WhisperModel = pytest.importorskip("faster_whisper").WhisperModel


async def test_faster_whisper_transcribes_a_real_french_audio_on_gpu(
    tmp_path: Path,
    french_dialogue_audio_path: Path,
) -> None:
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    with french_dialogue_audio_path.open("rb") as audio:
        location = storage.save(
            uuid4(),
            audio,
            extension=french_dialogue_audio_path.suffix.removeprefix(".").lower(),
        )
    transcriber = FasterWhisperTranscriber(
        model=WhisperModel(
            "large-v3-turbo",
            device="cuda",
            compute_type="float16",
        ),
        storage=storage,
    )

    output = await transcriber.transcribe(location)

    assert isinstance(output, TranscriptionOutput)
    assert output.result["language"] == "fr"
    assert isinstance(output.result["text"], str)
    assert output.result["text"]
    assert isinstance(output.result["segments"], list)
    assert output.result["segments"]
