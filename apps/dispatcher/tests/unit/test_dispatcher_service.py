from datetime import UTC, datetime
import logging
from typing import cast
from uuid import UUID

import pytest

from dispatcher.models import DispatchBatchResult, DispatchJobSnapshot
from dispatcher.service import DispatcherService
from transcribe_ai_shared import (
    JobStreamMessage,
    JobType,
    RedisConnectionError,
    TranscriptionStreams,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

FIRST_JOB_UUID = UUID("00000000-0000-0000-0000-000000000001")
SECOND_JOB_UUID = UUID("00000000-0000-0000-0000-000000000002")
LAST_DISPATCHED_AT = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)


class RecordingJobStore:
    def __init__(
        self,
        jobs: list[DispatchJobSnapshot],
        *,
        mark_outcomes: list[bool | Exception] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.jobs = jobs
        self.mark_outcomes = list(mark_outcomes or [])
        self.events = events
        self.find_limits: list[int] = []
        self.mark_calls: list[DispatchJobSnapshot] = []

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[DispatchJobSnapshot]:
        self.find_limits.append(limit)
        return self.jobs[:limit]

    async def mark_dispatched(self, snapshot: DispatchJobSnapshot) -> bool:
        if self.events is not None:
            self.events.append("mark_dispatched")
        self.mark_calls.append(snapshot)
        outcome = self.mark_outcomes.pop(0) if self.mark_outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingStreams:
    def __init__(
        self,
        *,
        publish_outcomes: list[str | Exception] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.publish_outcomes = list(publish_outcomes or [])
        self.events = events
        self.published_messages: list[JobStreamMessage] = []

    async def publish(self, message: JobStreamMessage) -> str:
        if self.events is not None:
            self.events.append("publish")
        self.published_messages.append(message)
        outcome = (
            self.publish_outcomes.pop(0) if self.publish_outcomes else "1700000000000-0"
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_job(
    *,
    job_uuid: UUID = FIRST_JOB_UUID,
    job_type: JobType = JobType.FAST,
    attempt_count: int = 0,
    last_dispatched_at: datetime | None = None,
) -> DispatchJobSnapshot:
    return DispatchJobSnapshot(
        job_uuid=job_uuid,
        job_type=job_type,
        attempt_count=attempt_count,
        last_dispatched_at=last_dispatched_at,
    )


def expected_message(snapshot: DispatchJobSnapshot) -> JobStreamMessage:
    return JobStreamMessage(
        job_uuid=snapshot.job_uuid,
        job_type=snapshot.job_type,
        attempt_count=snapshot.attempt_count,
    )


def make_service(
    store: RecordingJobStore,
    streams: RecordingStreams,
) -> DispatcherService:
    return DispatcherService(
        job_store=store,
        streams=cast(TranscriptionStreams, streams),
    )


async def test_dispatch_batch_returns_empty_result_when_no_job_is_available() -> None:
    store = RecordingJobStore([])
    streams = RecordingStreams()

    result = await make_service(store, streams).dispatch_batch(batch_size=20)

    assert result == DispatchBatchResult(0, 0, 0, 0, 0)
    assert store.find_limits == [20]
    assert streams.published_messages == []
    assert store.mark_calls == []


@pytest.mark.parametrize("job_type", [JobType.FAST, JobType.BATCH])
async def test_dispatch_batch_publishes_to_the_stream_selected_by_job_type(
    job_type: JobType,
) -> None:
    job = make_job(job_type=job_type)
    store = RecordingJobStore([job])
    streams = RecordingStreams()

    result = await make_service(store, streams).dispatch_batch(batch_size=1)

    assert streams.published_messages == [expected_message(job)]
    assert store.mark_calls == [job]
    assert result == DispatchBatchResult(1, 1, 1, 0, 0)


async def test_dispatch_batch_does_not_expose_reconciliation_guard_in_redis() -> None:
    job = make_job(attempt_count=7, last_dispatched_at=LAST_DISPATCHED_AT)
    store = RecordingJobStore([job])
    streams = RecordingStreams()

    await make_service(store, streams).dispatch_batch(batch_size=1)

    assert streams.published_messages == [expected_message(job)]
    assert not hasattr(streams.published_messages[0], "last_dispatched_at")
    assert store.mark_calls == [job]


async def test_dispatch_batch_does_not_confirm_after_redis_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RecordingJobStore([make_job()])
    streams = RecordingStreams(
        publish_outcomes=[
            RedisConnectionError("redis://user:secret@redis:6379 indisponible"),
        ],
    )

    with caplog.at_level(logging.WARNING):
        result = await make_service(store, streams).dispatch_batch(batch_size=1)

    assert store.mark_calls == []
    assert result == DispatchBatchResult(1, 0, 0, 0, 1)
    assert "RedisConnectionError" in caplog.text
    assert "secret" not in caplog.text


async def test_dispatch_batch_marks_dispatched_only_after_xadd() -> None:
    events: list[str] = []
    store = RecordingJobStore([make_job()], events=events)
    streams = RecordingStreams(events=events)

    await make_service(store, streams).dispatch_batch(batch_size=1)

    assert events == ["publish", "mark_dispatched"]


async def test_dispatch_batch_passes_the_complete_observed_snapshot() -> None:
    job = make_job(attempt_count=7, last_dispatched_at=LAST_DISPATCHED_AT)
    store = RecordingJobStore([job])
    streams = RecordingStreams()

    await make_service(store, streams).dispatch_batch(batch_size=1)

    assert store.mark_calls == [job]


async def test_dispatch_batch_continues_after_one_job_fails() -> None:
    first_job = make_job()
    second_job = make_job(
        job_uuid=SECOND_JOB_UUID,
        job_type=JobType.BATCH,
    )
    store = RecordingJobStore([first_job, second_job])
    streams = RecordingStreams(
        publish_outcomes=[
            RedisConnectionError("redis unavailable"),
            "1700000000001-0",
        ],
    )

    result = await make_service(store, streams).dispatch_batch(batch_size=2)

    assert streams.published_messages == [
        expected_message(first_job),
        expected_message(second_job),
    ]
    assert store.mark_calls == [second_job]
    assert result == DispatchBatchResult(2, 1, 1, 0, 1)


async def test_dispatch_batch_continues_after_one_confirmation_fails() -> None:
    first_job = make_job()
    second_job = make_job(job_uuid=SECOND_JOB_UUID)
    store = RecordingJobStore(
        [first_job, second_job],
        mark_outcomes=[RuntimeError("database unavailable"), True],
    )
    streams = RecordingStreams(
        publish_outcomes=["1700000000000-0", "1700000000001-0"],
    )

    result = await make_service(store, streams).dispatch_batch(batch_size=2)

    assert store.mark_calls == [first_job, second_job]
    assert result == DispatchBatchResult(2, 2, 1, 0, 1)


async def test_dispatch_batch_treats_a_rejected_confirmation_as_stale() -> None:
    store = RecordingJobStore([make_job()], mark_outcomes=[False])
    streams = RecordingStreams()

    result = await make_service(store, streams).dispatch_batch(batch_size=1)

    assert result == DispatchBatchResult(1, 1, 0, 1, 0)


@pytest.mark.parametrize("batch_size", [True, 0, -1])
async def test_dispatch_batch_rejects_an_invalid_batch_size(batch_size: int) -> None:
    store = RecordingJobStore([])
    streams = RecordingStreams()

    with pytest.raises(ValueError, match="batch_size"):
        await make_service(store, streams).dispatch_batch(batch_size=batch_size)

    assert store.find_limits == []
