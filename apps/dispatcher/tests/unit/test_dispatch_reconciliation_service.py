from datetime import UTC, datetime
import logging
from uuid import UUID

import pytest

from dispatcher.models import ReconciliationBatchResult, StaleDispatchSnapshot
from dispatcher.reconciliation import DispatchReconciliationService


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

FIRST_JOB_UUID = UUID("00000000-0000-0000-0000-000000000001")
SECOND_JOB_UUID = UUID("00000000-0000-0000-0000-000000000002")
THIRD_JOB_UUID = UUID("00000000-0000-0000-0000-000000000003")
LAST_DISPATCHED_AT = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)


class RecordingReconciliationStore:
    def __init__(
        self,
        jobs: list[StaleDispatchSnapshot],
        *,
        rearm_outcomes: list[bool | Exception] | None = None,
    ) -> None:
        self.jobs = jobs
        self.rearm_outcomes = list(rearm_outcomes or [])
        self.find_calls: list[tuple[int, int]] = []
        self.rearm_calls: list[tuple[StaleDispatchSnapshot, int]] = []

    async def find_stale_dispatched_jobs(
        self,
        limit: int,
        reconciliation_timeout_seconds: int,
    ) -> list[StaleDispatchSnapshot]:
        self.find_calls.append((limit, reconciliation_timeout_seconds))
        return self.jobs[:limit]

    async def rearm_stale_dispatch(
        self,
        snapshot: StaleDispatchSnapshot,
        reconciliation_timeout_seconds: int,
    ) -> bool:
        self.rearm_calls.append((snapshot, reconciliation_timeout_seconds))
        outcome = self.rearm_outcomes.pop(0) if self.rearm_outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_snapshot(
    job_uuid: UUID = FIRST_JOB_UUID,
    attempt_count: int = 0,
) -> StaleDispatchSnapshot:
    return StaleDispatchSnapshot(
        job_uuid=job_uuid,
        attempt_count=attempt_count,
        last_dispatched_at=LAST_DISPATCHED_AT,
    )


async def test_reconcile_batch_returns_empty_result_when_no_job_is_stale() -> None:
    store = RecordingReconciliationStore([])
    service = DispatchReconciliationService(job_store=store)

    result = await service.reconcile_batch(
        batch_size=20,
        reconciliation_timeout_seconds=90,
    )

    assert result == ReconciliationBatchResult(0, 0, 0, 0)
    assert store.find_calls == [(20, 90)]
    assert store.rearm_calls == []


async def test_reconcile_batch_rearms_each_selected_snapshot() -> None:
    first_job = make_snapshot(attempt_count=1)
    second_job = make_snapshot(SECOND_JOB_UUID, attempt_count=2)
    store = RecordingReconciliationStore([first_job, second_job])
    service = DispatchReconciliationService(job_store=store)

    result = await service.reconcile_batch(
        batch_size=2,
        reconciliation_timeout_seconds=90,
    )

    assert store.rearm_calls == [(first_job, 90), (second_job, 90)]
    assert result == ReconciliationBatchResult(2, 2, 0, 0)


async def test_reconcile_batch_treats_a_rejected_cas_as_stale() -> None:
    job = make_snapshot()
    store = RecordingReconciliationStore([job], rearm_outcomes=[False])

    result = await DispatchReconciliationService(job_store=store).reconcile_batch(
        batch_size=1, reconciliation_timeout_seconds=90
    )

    assert result == ReconciliationBatchResult(1, 0, 1, 0)


async def test_reconcile_batch_continues_after_an_individual_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_job = make_snapshot()
    second_job = make_snapshot(SECOND_JOB_UUID)
    third_job = make_snapshot(THIRD_JOB_UUID)
    store = RecordingReconciliationStore(
        [first_job, second_job, third_job],
        rearm_outcomes=[
            RuntimeError("postgresql://user:secret@db unavailable"),
            False,
            True,
        ],
    )

    with caplog.at_level(logging.WARNING):
        result = await DispatchReconciliationService(job_store=store).reconcile_batch(
            batch_size=3, reconciliation_timeout_seconds=90
        )

    assert store.rearm_calls == [
        (first_job, 90),
        (second_job, 90),
        (third_job, 90),
    ]
    assert result == ReconciliationBatchResult(3, 1, 1, 1)
    error_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "reconciliation"
        and getattr(record, "action", None) == "failed"
    )
    assert error_record.error_type == "RuntimeError"
    assert error_record.job_uuid == str(FIRST_JOB_UUID)
    assert "secret" not in caplog.text


@pytest.mark.parametrize("batch_size", [True, 0, -1])
async def test_reconcile_batch_rejects_an_invalid_batch_size(
    batch_size: int,
) -> None:
    store = RecordingReconciliationStore([])

    with pytest.raises(ValueError, match="batch_size"):
        await DispatchReconciliationService(job_store=store).reconcile_batch(
            batch_size=batch_size,
            reconciliation_timeout_seconds=90,
        )

    assert store.find_calls == []


@pytest.mark.parametrize("timeout_seconds", [True, 0, -1])
async def test_reconcile_batch_rejects_an_invalid_timeout(
    timeout_seconds: int,
) -> None:
    store = RecordingReconciliationStore([])

    with pytest.raises(ValueError, match="reconciliation_timeout_seconds"):
        await DispatchReconciliationService(job_store=store).reconcile_batch(
            batch_size=20,
            reconciliation_timeout_seconds=timeout_seconds,
        )

    assert store.find_calls == []
