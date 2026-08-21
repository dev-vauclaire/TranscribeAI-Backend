from uuid import UUID

import pytest

from transcribe_ai_shared import AudioLocation, TranscriptionOutput
from transcribe_ai_shared.worker.testing import FakeTranscriber


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
LOCATION = AudioLocation(f"{JOB_UUID}/input.wav")


async def test_fake_transcriber_returns_configured_output_and_records_calls() -> None:
    output = TranscriptionOutput(result={"text": "configured"})
    transcriber = FakeTranscriber(output)

    result = await transcriber.transcribe(LOCATION)

    assert result is output
    assert transcriber.calls == (LOCATION,)


async def test_fake_transcriber_has_a_deterministic_development_output() -> None:
    result = await FakeTranscriber().transcribe(LOCATION)

    assert result == TranscriptionOutput(result={"text": "fake transcription"})


async def test_fake_transcriber_can_simulate_a_failure() -> None:
    error = RuntimeError("model unavailable")
    transcriber = FakeTranscriber(error=error)

    with pytest.raises(RuntimeError) as raised:
        await transcriber.transcribe(LOCATION)

    assert raised.value is error
    assert transcriber.calls == (LOCATION,)
