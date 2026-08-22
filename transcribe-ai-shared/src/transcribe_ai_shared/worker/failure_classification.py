from transcribe_ai_shared.worker.exceptions import (
    PermanentTranscriptionError,
    RetryableTranscriptionError,
)
from transcribe_ai_shared.worker.models import (
    ClassifiedTranscriptionFailure,
    TranscriptionFailureCategory,
)


UNEXPECTED_TRANSCRIPTION_ERROR_CODE = "TRANSCRIPTION_UNEXPECTED_ERROR"


def classify_transcription_failure(
    error: Exception,
) -> ClassifiedTranscriptionFailure:
    """Classe une erreur moteur sans persister son message potentiellement sensible."""
    if not isinstance(error, Exception):
        raise TypeError("error doit être une Exception")

    if isinstance(error, PermanentTranscriptionError):
        return ClassifiedTranscriptionFailure(
            category=TranscriptionFailureCategory.PERMANENT,
            error_code=error.error_code,
        )
    if isinstance(error, RetryableTranscriptionError):
        return ClassifiedTranscriptionFailure(
            category=TranscriptionFailureCategory.RETRYABLE,
            error_code=error.error_code,
        )
    return ClassifiedTranscriptionFailure(
        category=TranscriptionFailureCategory.RETRYABLE,
        error_code=UNEXPECTED_TRANSCRIPTION_ERROR_CODE,
    )
