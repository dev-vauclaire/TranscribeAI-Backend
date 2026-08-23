from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

import pytest

from dispatcher.models import ExpiredJobSnapshot, RecoveryBatchResult
from dispatcher.recovery import LeaseRecoveryService
from transcribe_ai_shared.queue.models import MAX_ATTEMPT_COUNT


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

FIRST_JOB_UUID = UUID("00000000-0000-0000-0000-000000000001")
SECOND_JOB_UUID = UUID("00000000-0000-0000-0000-000000000002")
CUTOFF = datetime(2026, 8, 23, 10, 30, tzinfo=UTC)


class RecordingExpiredJobStore:
    def __init__(
        self,
        jobs: list[ExpiredJobSnapshot],
        *,
        outcomes: list[bool | Exception] | None = None,
    ) -> None:
        self.jobs = jobs
        self.outcomes = list(outcomes or [])
        self.find_calls: list[int] = []
        self.recover_calls: list[tuple[ExpiredJobSnapshot, bool]] = []

    async def find_expired_jobs(
        self,
        limit: int,
    ) -> list[ExpiredJobSnapshot]:
        self.find_calls.append(limit)
        return self.jobs[:limit]

    async def recover_expired_job(
        self,
        snapshot: ExpiredJobSnapshot,
        *,
        should_retry: bool,
    ) -> bool:
        self.recover_calls.append((snapshot, should_retry))
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_snapshot(
    *,
    job_uuid: UUID = FIRST_JOB_UUID,
    attempt_count: int = 0,
) -> ExpiredJobSnapshot:
    return ExpiredJobSnapshot(
        job_uuid=job_uuid,
        attempt_count=attempt_count,
        lease_expires_at=CUTOFF - timedelta(seconds=1),
    )


def make_service(store: RecordingExpiredJobStore) -> LeaseRecoveryService:
    return LeaseRecoveryService(job_store=store)


async def test_recover_batch_returns_empty_result_when_no_lease_is_expired() -> None:
    store = RecordingExpiredJobStore([])

    result = await make_service(store).recover_batch(batch_size=20, max_attempts=3)

    assert result == RecoveryBatchResult(0, 0, 0, 0, 0)
    assert store.find_calls == [20]
    assert store.recover_calls == []


async def test_recover_batch_requeues_when_an_attempt_remains() -> None:
    job = make_snapshot(attempt_count=1)
    store = RecordingExpiredJobStore([job])

    result = await make_service(store).recover_batch(batch_size=10, max_attempts=3)

    assert store.recover_calls == [(job, True)]
    assert result == RecoveryBatchResult(1, 1, 0, 0, 0)


async def test_recover_batch_fails_when_no_attempt_remains() -> None:
    job = make_snapshot(attempt_count=2)
    store = RecordingExpiredJobStore([job])

    result = await make_service(store).recover_batch(batch_size=10, max_attempts=3)

    assert store.recover_calls == [(job, False)]
    assert result == RecoveryBatchResult(1, 0, 1, 0, 0)


@pytest.mark.parametrize(
    ("attempt_count", "max_attempts", "should_retry"),
    [
        (0, 1, False),
        (0, 2, True),
        (1, 2, False),
        (MAX_ATTEMPT_COUNT - 1, MAX_ATTEMPT_COUNT + 1, True),
        (MAX_ATTEMPT_COUNT, MAX_ATTEMPT_COUNT + 1, False),
    ],
)
async def test_recover_batch_classifies_the_observed_attempt(
    attempt_count: int,
    max_attempts: int,
    should_retry: bool,
) -> None:
    job = make_snapshot(attempt_count=attempt_count)
    store = RecordingExpiredJobStore([job])

    await make_service(store).recover_batch(
        batch_size=1,
        max_attempts=max_attempts,
    )

    assert store.recover_calls == [(job, should_retry)]


async def test_recover_batch_limits_the_selected_batch() -> None:
    first_job = make_snapshot()
    second_job = make_snapshot(job_uuid=SECOND_JOB_UUID)
    store = RecordingExpiredJobStore([first_job, second_job])

    result = await make_service(store).recover_batch(batch_size=1, max_attempts=3)

    assert store.find_calls == [1]
    assert store.recover_calls == [(first_job, True)]
    assert result == RecoveryBatchResult(1, 1, 0, 0, 0)


async def test_recover_batch_treats_a_rejected_transition_as_stale() -> None:
    job = make_snapshot()
    store = RecordingExpiredJobStore([job], outcomes=[False])

    result = await make_service(store).recover_batch(batch_size=1, max_attempts=3)

    assert result == RecoveryBatchResult(1, 0, 0, 1, 0)


async def test_recover_batch_continues_after_an_individual_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_job = make_snapshot()
    second_job = make_snapshot(job_uuid=SECOND_JOB_UUID, attempt_count=2)
    store = RecordingExpiredJobStore(
        [first_job, second_job],
        outcomes=[RuntimeError("postgresql://user:secret@db unavailable"), True],
    )

    with caplog.at_level(logging.WARNING):
        result = await make_service(store).recover_batch(
            batch_size=2,
            max_attempts=3,
        )

    assert len(store.recover_calls) == 2
    assert result == RecoveryBatchResult(2, 0, 1, 0, 1)
    error_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "lease_recovery"
        and getattr(record, "action", None) == "failed"
    )
    assert error_record.error_type == "RuntimeError"
    assert error_record.job_uuid == str(FIRST_JOB_UUID)
    assert "secret" not in caplog.text


@pytest.mark.parametrize("batch_size", [True, 0, -1])
async def test_recover_batch_rejects_an_invalid_batch_size(batch_size: int) -> None:
    store = RecordingExpiredJobStore([])

    with pytest.raises(ValueError, match="batch_size"):
        await make_service(store).recover_batch(
            batch_size=batch_size,
            max_attempts=3,
        )

    assert store.find_calls == []


@pytest.mark.parametrize(
    "max_attempts",
    [True, 0, -1, MAX_ATTEMPT_COUNT + 2],
)
async def test_recover_batch_rejects_invalid_max_attempts(max_attempts: int) -> None:
    store = RecordingExpiredJobStore([])

    with pytest.raises(ValueError, match="max_attempts"):
        await make_service(store).recover_batch(
            batch_size=1,
            max_attempts=max_attempts,
        )

    assert store.find_calls == []
