import pytest

from transcribe_ai_shared.queue.models import MAX_ATTEMPT_COUNT
from transcribe_ai_shared.retry_policy import (
    MAX_TOTAL_ATTEMPTS,
    can_schedule_next_attempt,
    validate_max_attempts,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("attempt_count", "max_attempts", "expected"),
    [
        (0, 1, False),
        (0, 3, True),
        (1, 3, True),
        (2, 3, False),
        (3, 2, False),
        (MAX_ATTEMPT_COUNT - 1, MAX_TOTAL_ATTEMPTS, True),
        (MAX_ATTEMPT_COUNT, MAX_TOTAL_ATTEMPTS, False),
    ],
)
def test_can_schedule_next_attempt_applies_the_zero_based_policy(
    attempt_count: int,
    max_attempts: int,
    expected: bool,
) -> None:
    assert (
        can_schedule_next_attempt(
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )
        is expected
    )


@pytest.mark.parametrize(
    "max_attempts",
    [True, 0, -1, 1.5, MAX_TOTAL_ATTEMPTS + 1],
)
def test_validate_max_attempts_rejects_invalid_values(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        validate_max_attempts(max_attempts)


@pytest.mark.parametrize(
    "attempt_count",
    [True, -1, 1.5, MAX_ATTEMPT_COUNT + 1],
)
def test_can_schedule_next_attempt_rejects_invalid_attempt_counts(
    attempt_count: int,
) -> None:
    with pytest.raises(ValueError, match="attempt_count"):
        can_schedule_next_attempt(
            attempt_count=attempt_count,
            max_attempts=3,
        )
