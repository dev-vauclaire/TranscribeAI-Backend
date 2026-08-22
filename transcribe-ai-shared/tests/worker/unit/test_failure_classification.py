import pytest

from transcribe_ai_shared import (
    ClassifiedTranscriptionFailure,
    PermanentTranscriptionError,
    RetryableTranscriptionError,
    TranscriptionFailureCategory,
    classify_transcription_failure,
)


pytestmark = pytest.mark.unit


def test_retryable_error_keeps_its_safe_code() -> None:
    failure = classify_transcription_failure(
        RetryableTranscriptionError("MODEL_TEMPORARILY_UNAVAILABLE")
    )

    assert failure.category is TranscriptionFailureCategory.RETRYABLE
    assert failure.error_code == "MODEL_TEMPORARILY_UNAVAILABLE"


def test_permanent_error_keeps_its_safe_code() -> None:
    failure = classify_transcription_failure(
        PermanentTranscriptionError("AUDIO_CANNOT_BE_TRANSCRIBED")
    )

    assert failure.category is TranscriptionFailureCategory.PERMANENT
    assert failure.error_code == "AUDIO_CANNOT_BE_TRANSCRIBED"


def test_unknown_error_is_retryable_without_exposing_its_message() -> None:
    failure = classify_transcription_failure(
        RuntimeError("token=secret-value path=/private/audio.wav")
    )

    assert failure.category is TranscriptionFailureCategory.RETRYABLE
    assert failure.error_code == "TRANSCRIPTION_UNEXPECTED_ERROR"
    assert "secret-value" not in failure.error_code
    assert "/private/audio.wav" not in failure.error_code


@pytest.mark.parametrize("invalid_code", ["   ", "token=secret", "/private/audio"])
@pytest.mark.parametrize(
    "error_type",
    [RetryableTranscriptionError, PermanentTranscriptionError],
)
def test_classified_errors_require_a_canonical_non_sensitive_code(
    error_type,
    invalid_code: str,
) -> None:
    with pytest.raises(ValueError, match="error_code"):
        error_type(invalid_code)


def test_classified_failure_rejects_a_raw_message_from_direct_callers() -> None:
    with pytest.raises(ValueError, match="error_code"):
        ClassifiedTranscriptionFailure(
            category=TranscriptionFailureCategory.RETRYABLE,
            error_code="connection failed: token=secret",
        )


def test_error_code_is_bounded() -> None:
    with pytest.raises(ValueError, match="128"):
        RetryableTranscriptionError("A" * 129)


def test_classifier_rejects_base_exceptions_that_must_bypass_retry() -> None:
    with pytest.raises(TypeError, match="Exception"):
        classify_transcription_failure(KeyboardInterrupt())  # type: ignore[arg-type]
