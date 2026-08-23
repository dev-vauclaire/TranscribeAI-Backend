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
) -> None:
    configured_audio_path = os.getenv("WORKER_LONG_FORM_GPU_TEST_AUDIO_PATH")
    if configured_audio_path is None:
        pytest.fail(
            "WORKER_LONG_FORM_GPU_TEST_AUDIO_PATH doit cibler un audio "
            "français multi-locuteurs"
        )
    audio_path = Path(configured_audio_path)
    if not audio_path.is_file() or not audio_path.suffix:
        pytest.fail("Le fichier audio GPU configuré est introuvable ou sans extension")

    hugging_face_token = os.getenv("WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN")
    if hugging_face_token is None:
        pytest.fail("WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN est requis")

    model_directory = Path(os.getenv("WORKER_TRANSCRIBER_MODEL_DIRECTORY", "/models"))
    storage = FileSystemAudioStorage(tmp_path / "transcriptions")
    with audio_path.open("rb") as audio:
        location = storage.save(
            uuid4(),
            audio,
            extension=audio_path.suffix.removeprefix(".").lower(),
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
    for previous, current in zip(segments, segments[1:], strict=False):
        previous_speaker = previous["speaker"]
        assert previous_speaker is None or previous_speaker != current["speaker"]
