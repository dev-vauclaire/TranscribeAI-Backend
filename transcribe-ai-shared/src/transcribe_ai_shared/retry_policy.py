from transcribe_ai_shared.queue.models import MAX_ATTEMPT_COUNT


MAX_TOTAL_ATTEMPTS = MAX_ATTEMPT_COUNT + 1


def validate_max_attempts(max_attempts: int) -> None:
    """Valide le nombre total d'exécutions autorisées pour un job."""
    if (
        type(max_attempts) is not int
        or max_attempts < 1
        or max_attempts > MAX_TOTAL_ATTEMPTS
    ):
        raise ValueError(
            f"max_attempts doit être compris entre 1 et {MAX_TOTAL_ATTEMPTS}"
        )


def can_schedule_next_attempt(*, attempt_count: int, max_attempts: int) -> bool:
    """Indique si une nouvelle tentative peut suivre la tentative observée."""
    validate_max_attempts(max_attempts)
    if (
        type(attempt_count) is not int
        or attempt_count < 0
        or attempt_count > MAX_ATTEMPT_COUNT
    ):
        raise ValueError(
            f"attempt_count doit être compris entre 0 et {MAX_ATTEMPT_COUNT}"
        )
    return attempt_count + 1 < max_attempts
