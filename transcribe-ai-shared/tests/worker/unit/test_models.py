import pytest

from transcribe_ai_shared import TranscriptionOutput


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("speaker_count", [0, 2])
def test_transcription_output_accepts_a_non_negative_speaker_count(
    speaker_count: int,
) -> None:
    output = TranscriptionOutput(
        result={"text": "bonjour"},
        speaker_count=speaker_count,
    )

    assert output.speaker_count == speaker_count


def test_transcription_output_keeps_speaker_count_optional() -> None:
    output = TranscriptionOutput(result={"text": "bonjour"})

    assert output.speaker_count is None


@pytest.mark.parametrize("invalid_speaker_count", [-1, True, 1.5])
def test_transcription_output_rejects_an_invalid_speaker_count(
    invalid_speaker_count: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="speaker_count doit être un entier positif ou nul",
    ):
        TranscriptionOutput(
            result={"text": "bonjour"},
            speaker_count=invalid_speaker_count,
        )
