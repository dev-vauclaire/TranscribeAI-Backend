import os
from pathlib import Path
from uuid import uuid4

from faster_whisper import WhisperModel
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


async def test_faster_whisper_transcribes_a_real_french_audio_on_gpu(
    tmp_path: Path,
) -> None:
    configured_audio_path = os.getenv("WORKER_FAST_GPU_TEST_AUDIO_PATH")
    if configured_audio_path is None:
        pytest.fail(
            "WORKER_FAST_GPU_TEST_AUDIO_PATH doit cibler un court audio français"
        )
    audio_path = Path(configured_audio_path)
    if not audio_path.is_file() or not audio_path.suffix:
        pytest.fail("Le fichier audio GPU configuré est introuvable ou sans extension")

    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    with audio_path.open("rb") as audio:
        location = storage.save(
            uuid4(),
            audio,
            extension=audio_path.suffix.removeprefix(".").lower(),
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
