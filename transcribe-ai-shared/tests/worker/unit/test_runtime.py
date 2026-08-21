import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

from transcribe_ai_shared import (
    AudioLocation,
    ClaimedJob,
    InvalidJobStreamMessageError,
    JobType,
    ReceivedJobStreamMessage,
    Transcriber,
    TranscriptionExecutionError,
    TranscriptionOutput,
    TranscriptionStreams,
    WorkerClaimRejected,
    WorkerIdle,
    WorkerJobStore,
    WorkerRuntime,
    WorkerTranscribed,
)
from transcribe_ai_shared.worker.testing import FakeTranscriber


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
REDIS_MESSAGE_ID = "1755770400000-0"
NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
LEASE_DURATION = timedelta(minutes=5)
GROUP_NAME = "transcription-workers"
WORKER_ID = "worker-fast-1"


def make_message() -> ReceivedJobStreamMessage:
    return ReceivedJobStreamMessage(
        redis_message_id=REDIS_MESSAGE_ID,
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=2,
    )


def make_claimed_job() -> ClaimedJob:
    return ClaimedJob(
        job_uuid=JOB_UUID,
        job_type=JobType.FAST,
        attempt_count=2,
        audio_location=AudioLocation(f"{JOB_UUID}/input.wav"),
    )


class RecordingStreams:
    def __init__(
        self,
        message: ReceivedJobStreamMessage | None = None,
        *,
        consume_error: Exception | None = None,
        ack_result: bool = True,
        events: list[str] | None = None,
    ) -> None:
        self.message = message
        self.consume_error = consume_error
        self.ack_result = ack_result
        self.events = events
        self.ensure_calls: list[tuple[JobType, str]] = []
        self.consume_calls: list[tuple[JobType, str, str, int | None]] = []
        self.ack_calls: list[tuple[str, ReceivedJobStreamMessage]] = []

    async def ensure_consumer_group(
        self,
        job_type: JobType,
        group_name: str,
    ) -> None:
        self.ensure_calls.append((job_type, group_name))

    async def consume(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        block_milliseconds: int | None = 5_000,
    ) -> ReceivedJobStreamMessage | None:
        if self.events is not None:
            self.events.append("consume")
        self.consume_calls.append(
            (job_type, group_name, consumer_name, block_milliseconds)
        )
        if self.consume_error is not None:
            raise self.consume_error
        return self.message

    async def ack_and_delete(
        self,
        group_name: str,
        message: ReceivedJobStreamMessage,
    ) -> bool:
        if self.events is not None:
            self.events.append("ack_and_delete")
        self.ack_calls.append((group_name, message))
        return self.ack_result


class RecordingJobStore:
    def __init__(
        self,
        claimed_job: ClaimedJob | None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.claimed_job = claimed_job
        self.events = events
        self.claim_calls: list[tuple[UUID, str, datetime]] = []

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ClaimedJob | None:
        if self.events is not None:
            self.events.append("claim")
        self.claim_calls.append((job_uuid, worker_id, lease_expires_at))
        return self.claimed_job


class BlockingTranscriber:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe(
        self,
        _audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        self.started.set()
        await self.release.wait()
        return TranscriptionOutput(result={"text": "completed"})


def make_runtime(
    streams: RecordingStreams,
    store: RecordingJobStore,
    transcriber: Transcriber,
    *,
    clock=lambda: NOW,
) -> WorkerRuntime:
    return WorkerRuntime(
        streams=cast(TranscriptionStreams, streams),
        job_store=cast(WorkerJobStore, store),
        transcriber=transcriber,
        job_type=JobType.FAST,
        group_name=GROUP_NAME,
        worker_id=WORKER_ID,
        lease_duration=LEASE_DURATION,
        clock=clock,
    )


async def test_initialize_creates_the_group_for_the_configured_stream() -> None:
    streams = RecordingStreams()
    runtime = make_runtime(
        streams,
        RecordingJobStore(None),
        FakeTranscriber(),
    )

    await runtime.initialize()

    assert streams.ensure_calls == [(JobType.FAST, GROUP_NAME)]


async def test_process_next_returns_idle_when_no_message_is_available() -> None:
    streams = RecordingStreams()
    store = RecordingJobStore(make_claimed_job())
    transcriber = FakeTranscriber()

    result = await make_runtime(streams, store, transcriber).process_next(
        block_milliseconds=250,
    )

    assert result == WorkerIdle()
    assert streams.consume_calls == [(JobType.FAST, GROUP_NAME, WORKER_ID, 250)]
    assert store.claim_calls == []
    assert transcriber.calls == ()
    assert streams.ack_calls == []


async def test_valid_message_is_claimed_then_transcribed_without_ack() -> None:
    events: list[str] = []
    message = make_message()
    claimed_job = make_claimed_job()
    streams = RecordingStreams(message, events=events)
    store = RecordingJobStore(claimed_job, events=events)
    output = TranscriptionOutput(result={"text": "hello"})

    class EventTranscriber:
        async def transcribe(
            self,
            audio_location: AudioLocation,
        ) -> TranscriptionOutput:
            events.append("transcribe")
            assert audio_location == claimed_job.audio_location
            return output

    result = await make_runtime(
        streams,
        store,
        EventTranscriber(),
    ).process_next()

    assert result == WorkerTranscribed(message, claimed_job, output)
    assert events == ["consume", "claim", "transcribe"]
    assert store.claim_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION),
    ]
    assert streams.ack_calls == []


async def test_rejected_claim_is_acked_without_calling_the_transcriber() -> None:
    message = make_message()
    streams = RecordingStreams(message)
    store = RecordingJobStore(None)
    transcriber = FakeTranscriber()

    result = await make_runtime(streams, store, transcriber).process_next()

    assert result == WorkerClaimRejected(message, removed_from_stream=True)
    assert transcriber.calls == ()
    assert streams.ack_calls == [(GROUP_NAME, message)]


async def test_missing_job_uses_the_same_safe_path_as_any_rejected_claim() -> None:
    message_for_missing_job = make_message()
    streams = RecordingStreams(message_for_missing_job, ack_result=False)
    store = RecordingJobStore(None)
    transcriber = FakeTranscriber()

    result = await make_runtime(streams, store, transcriber).process_next()

    assert result == WorkerClaimRejected(
        message_for_missing_job,
        removed_from_stream=False,
    )
    assert transcriber.calls == ()
    assert streams.ack_calls == [(GROUP_NAME, message_for_missing_job)]


async def test_invalid_payload_error_is_propagated_before_postgres_access() -> None:
    error = InvalidJobStreamMessageError(
        "job_uuid doit être un UUID canonique",
        redis_message_id=REDIS_MESSAGE_ID,
    )
    streams = RecordingStreams(consume_error=error)
    store = RecordingJobStore(make_claimed_job())
    transcriber = FakeTranscriber()

    with pytest.raises(InvalidJobStreamMessageError) as raised:
        await make_runtime(streams, store, transcriber).process_next()

    assert raised.value is error
    assert store.claim_calls == []
    assert transcriber.calls == ()
    assert streams.ack_calls == []


async def test_transcriber_error_is_classified_and_never_acknowledged() -> None:
    message = make_message()
    cause = RuntimeError("model unavailable")
    streams = RecordingStreams(message)
    transcriber = FakeTranscriber(error=cause)

    with pytest.raises(TranscriptionExecutionError) as raised:
        await make_runtime(
            streams,
            RecordingJobStore(make_claimed_job()),
            transcriber,
        ).process_next()

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.redis_message_id == REDIS_MESSAGE_ID
    assert raised.value.__cause__ is cause
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)
    assert streams.ack_calls == []


async def test_message_is_not_acked_while_transcription_is_in_progress() -> None:
    streams = RecordingStreams(make_message())
    transcriber = BlockingTranscriber()
    runtime = make_runtime(
        streams,
        RecordingJobStore(make_claimed_job()),
        transcriber,
    )

    task = asyncio.create_task(runtime.process_next())
    await transcriber.started.wait()

    assert task.done() is False
    assert streams.ack_calls == []

    transcriber.release.set()
    result = await task
    assert isinstance(result, WorkerTranscribed)
    assert streams.ack_calls == []


async def test_lease_expiry_is_normalized_to_utc() -> None:
    local_clock = datetime(
        2026,
        8,
        21,
        14,
        tzinfo=timezone(timedelta(hours=2)),
    )
    store = RecordingJobStore(make_claimed_job())

    await make_runtime(
        RecordingStreams(make_message()),
        store,
        FakeTranscriber(),
        clock=lambda: local_clock,
    ).process_next()

    assert store.claim_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION),
    ]


async def test_naive_clock_is_rejected_without_claim_or_ack() -> None:
    streams = RecordingStreams(make_message())
    store = RecordingJobStore(make_claimed_job())

    with pytest.raises(ValueError, match="clock"):
        await make_runtime(
            streams,
            store,
            FakeTranscriber(),
            clock=lambda: datetime(2026, 8, 21, 12),
        ).process_next()

    assert store.claim_calls == []
    assert streams.ack_calls == []


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("job_type", "FAST"),
        ("group_name", "   "),
        ("worker_id", ""),
        ("lease_duration", timedelta(0)),
    ],
)
async def test_runtime_rejects_invalid_identity_or_lease(
    parameter: str,
    value: object,
) -> None:
    arguments = {
        "streams": cast(TranscriptionStreams, RecordingStreams()),
        "job_store": cast(WorkerJobStore, RecordingJobStore(None)),
        "transcriber": FakeTranscriber(),
        "job_type": JobType.FAST,
        "group_name": GROUP_NAME,
        "worker_id": WORKER_ID,
        "lease_duration": LEASE_DURATION,
    }
    arguments[parameter] = value

    with pytest.raises(ValueError, match=parameter):
        WorkerRuntime(**arguments)  # type: ignore[arg-type]
