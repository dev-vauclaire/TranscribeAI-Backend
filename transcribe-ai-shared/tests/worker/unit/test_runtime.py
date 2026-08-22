import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

import transcribe_ai_shared.worker.runtime as runtime_module
from transcribe_ai_shared import (
    AudioLocation,
    ClaimedJob,
    ClassifiedTranscriptionFailure,
    InvalidJobStreamMessageError,
    JobStatus,
    JobType,
    PermanentTranscriptionError,
    ReceivedJobStreamMessage,
    RetryableTranscriptionError,
    Transcriber,
    TranscriptionCompleter,
    TranscriptionCompletionError,
    TranscriptionFailureCategory,
    TranscriptionFailureHandler,
    TranscriptionFailureResolution,
    TranscriptionFailureTransitionError,
    TranscriptionOutput,
    TranscriptionStreams,
    WorkerClaimRejected,
    WorkerCompleted,
    WorkerFailed,
    WorkerHeartbeatError,
    WorkerIdle,
    WorkerJobStore,
    WorkerJobTypeMismatchError,
    WorkerLeaseLostError,
    WorkerRetryScheduled,
    WorkerRuntime,
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
        renew_outcomes: list[bool | Exception] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.claimed_job = claimed_job
        self.renew_outcomes = list(renew_outcomes or [])
        self.events = events
        self.claim_calls: list[tuple[UUID, str, datetime, int]] = []
        self.renew_calls: list[tuple[UUID, str, datetime, int]] = []
        self.renewed = asyncio.Event()

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> ClaimedJob | None:
        if self.events is not None:
            self.events.append("claim")
        self.claim_calls.append(
            (
                job_uuid,
                worker_id,
                lease_expires_at,
                expected_attempt_count,
            )
        )
        return self.claimed_job

    async def renew_lease(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> bool:
        if self.events is not None:
            self.events.append("renew_lease")
        self.renew_calls.append(
            (
                job_uuid,
                worker_id,
                lease_expires_at,
                expected_attempt_count,
            )
        )
        self.renewed.set()
        outcome = self.renew_outcomes.pop(0) if self.renew_outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingCompleter:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.calls: list[tuple[ClaimedJob, str, TranscriptionOutput]] = []

    async def complete(
        self,
        *,
        job: ClaimedJob,
        worker_id: str,
        output: TranscriptionOutput,
    ) -> None:
        if self.events is not None:
            self.events.append("complete")
        self.calls.append((job, worker_id, output))
        if self.error is not None:
            raise self.error


class RecordingFailureHandler:
    def __init__(
        self,
        *,
        resolution: TranscriptionFailureResolution | None = None,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.resolution = resolution or TranscriptionFailureResolution(
            status=JobStatus.FAILED,
            attempt_count=2,
        )
        self.error = error
        self.events = events
        self.calls: list[tuple[ClaimedJob, str, ClassifiedTranscriptionFailure]] = []

    async def handle(
        self,
        *,
        job: ClaimedJob,
        worker_id: str,
        failure: ClassifiedTranscriptionFailure,
    ) -> TranscriptionFailureResolution:
        if self.events is not None:
            self.events.append("handle_failure")
        self.calls.append((job, worker_id, failure))
        if self.error is not None:
            raise self.error
        return self.resolution


class BlockingTranscriber:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def transcribe(
        self,
        _audio_location: AudioLocation,
    ) -> TranscriptionOutput:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return TranscriptionOutput(result={"text": "completed"})


class ControlledSleep:
    """Expose chaque échéance sans dépendre du temps réel dans les tests."""

    def __init__(self) -> None:
        self.calls: list[float] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._ticks: asyncio.Queue[None] = asyncio.Queue()

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.started.set()
        try:
            await self._ticks.get()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    def tick(self) -> None:
        self._ticks.put_nowait(None)


def make_runtime(
    streams: RecordingStreams,
    store: RecordingJobStore,
    transcriber: Transcriber,
    *,
    completer: TranscriptionCompleter | None = None,
    failure_handler: TranscriptionFailureHandler | None = None,
    heartbeat_interval: timedelta = timedelta(minutes=1),
    clock=lambda: NOW,
    sleep=asyncio.sleep,
) -> WorkerRuntime:
    return WorkerRuntime(
        streams=cast(TranscriptionStreams, streams),
        job_store=cast(WorkerJobStore, store),
        transcriber=transcriber,
        completer=completer or RecordingCompleter(),
        failure_handler=failure_handler or RecordingFailureHandler(),
        job_type=JobType.FAST,
        group_name=GROUP_NAME,
        worker_id=WORKER_ID,
        lease_duration=LEASE_DURATION,
        heartbeat_interval=heartbeat_interval,
        clock=clock,
        sleep=sleep,
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
    completer = RecordingCompleter()

    result = await make_runtime(
        streams,
        store,
        transcriber,
        completer=completer,
    ).process_next(
        block_milliseconds=250,
    )

    assert result == WorkerIdle()
    assert streams.consume_calls == [(JobType.FAST, GROUP_NAME, WORKER_ID, 250)]
    assert store.claim_calls == []
    assert transcriber.calls == ()
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_valid_message_is_transcribed_completed_then_acked() -> None:
    events: list[str] = []
    message = make_message()
    claimed_job = make_claimed_job()
    streams = RecordingStreams(message, events=events)
    store = RecordingJobStore(claimed_job, events=events)
    output = TranscriptionOutput(result={"text": "hello"})
    completer = RecordingCompleter(events=events)

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
        completer=completer,
    ).process_next()

    assert result == WorkerCompleted(
        message,
        claimed_job,
        output,
        removed_from_stream=True,
    )
    assert events == [
        "consume",
        "claim",
        "transcribe",
        "renew_lease",
        "complete",
        "ack_and_delete",
    ]
    assert store.claim_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]
    assert completer.calls == [(claimed_job, WORKER_ID, output)]
    assert store.renew_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]
    assert streams.ack_calls == [(GROUP_NAME, message)]


async def test_completed_result_exposes_when_redis_entry_was_already_absent() -> None:
    message = make_message()
    claimed_job = make_claimed_job()
    output = TranscriptionOutput(result={"text": "hello"})
    streams = RecordingStreams(message, ack_result=False)

    result = await make_runtime(
        streams,
        RecordingJobStore(claimed_job),
        FakeTranscriber(output),
    ).process_next()

    assert result == WorkerCompleted(
        message,
        claimed_job,
        output,
        removed_from_stream=False,
    )
    assert streams.ack_calls == [(GROUP_NAME, message)]


async def test_recovered_message_from_another_stream_is_rejected_without_ack() -> None:
    message = ReceivedJobStreamMessage(
        redis_message_id=REDIS_MESSAGE_ID,
        job_uuid=JOB_UUID,
        job_type=JobType.BATCH,
        attempt_count=2,
    )
    streams = RecordingStreams(message)
    store = RecordingJobStore(make_claimed_job())
    transcriber = FakeTranscriber()
    completer = RecordingCompleter()

    with pytest.raises(WorkerJobTypeMismatchError) as raised:
        await make_runtime(
            streams,
            store,
            transcriber,
            completer=completer,
        ).process_next()

    assert raised.value.expected_job_type is JobType.FAST
    assert raised.value.actual_job_type is JobType.BATCH
    assert store.claim_calls == []
    assert transcriber.calls == ()
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_claim_refused_for_completed_job_is_acked_without_inference() -> None:
    message = make_message()
    streams = RecordingStreams(message)
    store = RecordingJobStore(None)
    transcriber = FakeTranscriber()
    completer = RecordingCompleter()

    result = await make_runtime(
        streams,
        store,
        transcriber,
        completer=completer,
    ).process_next()

    assert result == WorkerClaimRejected(message, removed_from_stream=True)
    assert transcriber.calls == ()
    assert completer.calls == []
    assert streams.ack_calls == [(GROUP_NAME, message)]


async def test_missing_job_uses_the_same_safe_path_as_any_rejected_claim() -> None:
    message_for_missing_job = make_message()
    streams = RecordingStreams(message_for_missing_job, ack_result=False)
    store = RecordingJobStore(None)
    transcriber = FakeTranscriber()
    completer = RecordingCompleter()

    result = await make_runtime(
        streams,
        store,
        transcriber,
        completer=completer,
    ).process_next()

    assert result == WorkerClaimRejected(
        message_for_missing_job,
        removed_from_stream=False,
    )
    assert transcriber.calls == ()
    assert completer.calls == []
    assert streams.ack_calls == [(GROUP_NAME, message_for_missing_job)]


async def test_invalid_payload_error_is_propagated_before_postgres_access() -> None:
    error = InvalidJobStreamMessageError(
        "job_uuid doit être un UUID canonique",
        redis_message_id=REDIS_MESSAGE_ID,
    )
    streams = RecordingStreams(consume_error=error)
    store = RecordingJobStore(make_claimed_job())
    transcriber = FakeTranscriber()
    completer = RecordingCompleter()

    with pytest.raises(InvalidJobStreamMessageError) as raised:
        await make_runtime(
            streams,
            store,
            transcriber,
            completer=completer,
        ).process_next()

    assert raised.value is error
    assert store.claim_calls == []
    assert transcriber.calls == ()
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_retryable_transcriber_error_is_persisted_then_acknowledged() -> None:
    events: list[str] = []
    message = make_message()
    claimed_job = make_claimed_job()
    cause = RetryableTranscriptionError("TRANSCRIPTION_MODEL_UNAVAILABLE")
    streams = RecordingStreams(message, events=events)
    store = RecordingJobStore(claimed_job, events=events)
    transcriber = FakeTranscriber(error=cause)
    completer = RecordingCompleter()
    failure_handler = RecordingFailureHandler(
        resolution=TranscriptionFailureResolution(
            status=JobStatus.QUEUED,
            attempt_count=3,
        ),
        events=events,
    )

    result = await make_runtime(
        streams,
        store,
        transcriber,
        completer=completer,
        failure_handler=failure_handler,
    ).process_next()

    failure = ClassifiedTranscriptionFailure(
        category=TranscriptionFailureCategory.RETRYABLE,
        error_code="TRANSCRIPTION_MODEL_UNAVAILABLE",
    )
    assert result == WorkerRetryScheduled(
        message=message,
        job=claimed_job,
        failure=failure,
        next_attempt_count=3,
        removed_from_stream=True,
    )
    assert events == ["consume", "claim", "handle_failure", "ack_and_delete"]
    assert transcriber.calls == (AudioLocation(f"{JOB_UUID}/input.wav"),)
    assert completer.calls == []
    assert failure_handler.calls == [(claimed_job, WORKER_ID, failure)]
    assert streams.ack_calls == [(GROUP_NAME, message)]


async def test_permanent_transcriber_error_marks_failed_then_acknowledges() -> None:
    events: list[str] = []
    message = make_message()
    claimed_job = make_claimed_job()
    streams = RecordingStreams(message, events=events)
    cause = PermanentTranscriptionError("TRANSCRIPTION_AUDIO_UNSUPPORTED")
    transcriber = FakeTranscriber(error=cause)
    completer = RecordingCompleter()
    failure_handler = RecordingFailureHandler(events=events)

    result = await make_runtime(
        streams,
        RecordingJobStore(claimed_job, events=events),
        transcriber,
        completer=completer,
        failure_handler=failure_handler,
    ).process_next()

    failure = ClassifiedTranscriptionFailure(
        category=TranscriptionFailureCategory.PERMANENT,
        error_code="TRANSCRIPTION_AUDIO_UNSUPPORTED",
    )
    assert result == WorkerFailed(
        message=message,
        job=claimed_job,
        failure=failure,
        removed_from_stream=True,
    )
    assert events == ["consume", "claim", "handle_failure", "ack_and_delete"]
    assert completer.calls == []
    assert failure_handler.calls == [(claimed_job, WORKER_ID, failure)]
    assert streams.ack_calls == [(GROUP_NAME, message)]


async def test_failure_transition_error_leaves_the_message_pending() -> None:
    message = make_message()
    streams = RecordingStreams(message)
    cause = RetryableTranscriptionError("TRANSCRIPTION_MODEL_UNAVAILABLE")
    transition_error = TranscriptionFailureTransitionError(JOB_UUID)
    failure_handler = RecordingFailureHandler(error=transition_error)

    with pytest.raises(TranscriptionFailureTransitionError) as raised:
        await make_runtime(
            streams,
            RecordingJobStore(make_claimed_job()),
            FakeTranscriber(error=cause),
            failure_handler=failure_handler,
        ).process_next()

    assert raised.value is transition_error
    assert raised.value.__context__ is None
    assert len(failure_handler.calls) == 1
    assert streams.ack_calls == []


async def test_completion_failure_is_propagated_without_ack() -> None:
    message = make_message()
    streams = RecordingStreams(message)
    transcriber = FakeTranscriber(TranscriptionOutput(result={"text": "completed"}))
    error = TranscriptionCompletionError(JOB_UUID)
    completer = RecordingCompleter(error=error)

    with pytest.raises(TranscriptionCompletionError) as raised:
        await make_runtime(
            streams,
            RecordingJobStore(make_claimed_job()),
            transcriber,
            completer=completer,
        ).process_next()

    assert raised.value is error
    assert completer.calls == [
        (
            make_claimed_job(),
            WORKER_ID,
            TranscriptionOutput(result={"text": "completed"}),
        )
    ]
    assert streams.ack_calls == []


async def test_message_is_not_acked_while_transcription_is_in_progress() -> None:
    streams = RecordingStreams(make_message())
    transcriber = BlockingTranscriber()
    completer = RecordingCompleter()
    runtime = make_runtime(
        streams,
        RecordingJobStore(make_claimed_job()),
        transcriber,
        completer=completer,
    )

    task = asyncio.create_task(runtime.process_next())
    await transcriber.started.wait()

    assert task.done() is False
    assert completer.calls == []
    assert streams.ack_calls == []

    transcriber.release.set()
    result = await task
    assert isinstance(result, WorkerCompleted)
    assert len(completer.calls) == 1
    assert streams.ack_calls == [(GROUP_NAME, make_message())]


async def test_heartbeat_renews_on_schedule_and_stops_after_success() -> None:
    message = make_message()
    streams = RecordingStreams(message)
    store = RecordingJobStore(make_claimed_job())
    transcriber = BlockingTranscriber()
    controlled_sleep = ControlledSleep()
    runtime = make_runtime(
        streams,
        store,
        transcriber,
        heartbeat_interval=timedelta(seconds=30),
        sleep=controlled_sleep,
    )

    processing = asyncio.create_task(runtime.process_next())
    await transcriber.started.wait()
    await controlled_sleep.started.wait()

    assert controlled_sleep.calls == [30.0]
    assert store.renew_calls == []

    controlled_sleep.tick()
    await store.renewed.wait()
    assert store.renew_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]

    transcriber.release.set()
    result = await processing

    assert isinstance(result, WorkerCompleted)
    assert controlled_sleep.cancelled.is_set()
    # Un renouvellement périodique, puis le CAS final avant la finalisation.
    assert store.renew_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]


async def test_heartbeat_stops_when_transcription_fails() -> None:
    cause = RuntimeError("model unavailable")
    controlled_sleep = ControlledSleep()

    class FailingAfterHeartbeatStartsTranscriber:
        async def transcribe(
            self,
            _audio_location: AudioLocation,
        ) -> TranscriptionOutput:
            await controlled_sleep.started.wait()
            raise cause

    streams = RecordingStreams(make_message())
    store = RecordingJobStore(make_claimed_job())
    completer = RecordingCompleter()
    failure_handler = RecordingFailureHandler(
        resolution=TranscriptionFailureResolution(
            status=JobStatus.QUEUED,
            attempt_count=3,
        )
    )

    result = await make_runtime(
        streams,
        store,
        FailingAfterHeartbeatStartsTranscriber(),
        completer=completer,
        failure_handler=failure_handler,
        sleep=controlled_sleep,
    ).process_next()

    assert isinstance(result, WorkerRetryScheduled)
    assert result.failure == ClassifiedTranscriptionFailure(
        category=TranscriptionFailureCategory.RETRYABLE,
        error_code="TRANSCRIPTION_UNEXPECTED_ERROR",
    )
    assert controlled_sleep.cancelled.is_set()
    assert store.renew_calls == []
    assert completer.calls == []
    assert len(failure_handler.calls) == 1
    assert streams.ack_calls == [(GROUP_NAME, make_message())]


async def test_external_cancellation_during_heartbeat_cleanup_is_propagated() -> None:
    cleanup_started = asyncio.Event()
    allow_cleanup_to_finish = asyncio.Event()

    async def sleep_with_observable_cleanup(_seconds: float) -> None:
        """Retient le heartbeat dans son cleanup pour annuler le parent à cet instant."""
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await allow_cleanup_to_finish.wait()
            raise

    streams = RecordingStreams(make_message())
    store = RecordingJobStore(make_claimed_job())
    transcriber = BlockingTranscriber()
    completer = RecordingCompleter()
    processing = asyncio.create_task(
        make_runtime(
            streams,
            store,
            transcriber,
            completer=completer,
            sleep=sleep_with_observable_cleanup,
        ).process_next()
    )

    await transcriber.started.wait()
    transcriber.release.set()
    await cleanup_started.wait()

    processing.cancel()
    await asyncio.sleep(0)
    allow_cleanup_to_finish.set()

    with pytest.raises(asyncio.CancelledError):
        await processing

    assert completer.calls == []
    assert streams.ack_calls == []
    worker_child_names = {
        f"transcription-{JOB_UUID}",
        f"heartbeat-{JOB_UUID}",
    }
    assert not any(
        task.get_name() in worker_child_names for task in asyncio.all_tasks()
    )


async def test_repeated_external_cancellation_drains_both_worker_children() -> None:
    transcription_started = asyncio.Event()
    transcription_cleanup_started = asyncio.Event()
    allow_transcription_cleanup = asyncio.Event()
    heartbeat_started = asyncio.Event()
    heartbeat_cleanup_started = asyncio.Event()
    allow_heartbeat_cleanup = asyncio.Event()

    class TranscriberWithObservableCleanup:
        async def transcribe(
            self,
            _audio_location: AudioLocation,
        ) -> TranscriptionOutput:
            transcription_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                transcription_cleanup_started.set()
                await allow_transcription_cleanup.wait()
                raise

    async def sleep_with_observable_cleanup(_seconds: float) -> None:
        heartbeat_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            heartbeat_cleanup_started.set()
            await allow_heartbeat_cleanup.wait()
            raise

    streams = RecordingStreams(make_message())
    completer = RecordingCompleter()
    processing = asyncio.create_task(
        make_runtime(
            streams,
            RecordingJobStore(make_claimed_job()),
            TranscriberWithObservableCleanup(),
            completer=completer,
            sleep=sleep_with_observable_cleanup,
        ).process_next()
    )

    await transcription_started.wait()
    await heartbeat_started.wait()
    processing.cancel()
    await transcription_cleanup_started.wait()

    processing.cancel()
    allow_transcription_cleanup.set()
    allow_heartbeat_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await processing

    worker_child_names = {
        f"transcription-{JOB_UUID}",
        f"heartbeat-{JOB_UUID}",
    }
    pending_worker_children = [
        task for task in asyncio.all_tasks() if task.get_name() in worker_child_names
    ]
    heartbeat_was_cleaned = heartbeat_cleanup_started.is_set()
    for task in pending_worker_children:
        task.cancel()
    await asyncio.gather(*pending_worker_children, return_exceptions=True)

    assert heartbeat_was_cleaned
    assert pending_worker_children == []
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_final_lease_renewal_rejects_completion_when_lease_was_lost() -> None:
    controlled_sleep = ControlledSleep()
    transcriber = BlockingTranscriber()
    store = RecordingJobStore(
        make_claimed_job(),
        renew_outcomes=[False],
    )
    streams = RecordingStreams(make_message())
    completer = RecordingCompleter()
    processing = asyncio.create_task(
        make_runtime(
            streams,
            store,
            transcriber,
            completer=completer,
            sleep=controlled_sleep,
        ).process_next()
    )

    await transcriber.started.wait()
    await controlled_sleep.started.wait()
    transcriber.release.set()

    with pytest.raises(WorkerLeaseLostError) as raised:
        await processing

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == 2
    assert controlled_sleep.cancelled.is_set()
    assert store.renew_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_final_lease_renewal_error_preserves_cause_without_completion() -> None:
    cause = RuntimeError("database unavailable")
    controlled_sleep = ControlledSleep()
    transcriber = BlockingTranscriber()
    store = RecordingJobStore(
        make_claimed_job(),
        renew_outcomes=[cause],
    )
    streams = RecordingStreams(make_message())
    completer = RecordingCompleter()
    processing = asyncio.create_task(
        make_runtime(
            streams,
            store,
            transcriber,
            completer=completer,
            sleep=controlled_sleep,
        ).process_next()
    )

    await transcriber.started.wait()
    await controlled_sleep.started.wait()
    transcriber.release.set()

    with pytest.raises(WorkerHeartbeatError) as raised:
        await processing

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == 2
    assert raised.value.__cause__ is cause
    assert controlled_sleep.cancelled.is_set()
    assert store.renew_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_lost_lease_cancels_inference_and_prevents_completion_and_ack() -> None:
    controlled_sleep = ControlledSleep()
    transcriber = BlockingTranscriber()
    store = RecordingJobStore(
        make_claimed_job(),
        renew_outcomes=[False],
    )
    streams = RecordingStreams(make_message())
    completer = RecordingCompleter()
    runtime = make_runtime(
        streams,
        store,
        transcriber,
        completer=completer,
        sleep=controlled_sleep,
    )

    processing = asyncio.create_task(runtime.process_next())
    await transcriber.started.wait()
    await controlled_sleep.started.wait()
    controlled_sleep.tick()

    with pytest.raises(WorkerLeaseLostError) as raised:
        await processing

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == 2
    assert transcriber.cancelled.is_set()
    assert store.renew_calls == [
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
    ]
    assert completer.calls == []
    assert streams.ack_calls == []


async def test_heartbeat_store_error_cancels_inference_and_preserves_cause() -> None:
    cause = RuntimeError("database unavailable")
    controlled_sleep = ControlledSleep()
    transcriber = BlockingTranscriber()
    store = RecordingJobStore(
        make_claimed_job(),
        renew_outcomes=[cause],
    )
    streams = RecordingStreams(make_message())

    processing = asyncio.create_task(
        make_runtime(
            streams,
            store,
            transcriber,
            sleep=controlled_sleep,
        ).process_next()
    )
    await transcriber.started.wait()
    await controlled_sleep.started.wait()
    controlled_sleep.tick()

    with pytest.raises(WorkerHeartbeatError) as raised:
        await processing

    assert raised.value.job_uuid == JOB_UUID
    assert raised.value.worker_id == WORKER_ID
    assert raised.value.expected_attempt_count == 2
    assert raised.value.__cause__ is cause
    assert transcriber.cancelled.is_set()
    assert streams.ack_calls == []


async def test_heartbeat_failure_does_not_mask_simultaneous_transcriber_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcription_cause = RuntimeError("model failed")
    heartbeat_cause = RuntimeError("database unavailable")
    store = RecordingJobStore(
        make_claimed_job(),
        renew_outcomes=[heartbeat_cause],
    )
    streams = RecordingStreams(make_message())
    completer = RecordingCompleter()
    failure_handler = RecordingFailureHandler()

    async def no_delay(_seconds: float) -> None:
        return None

    async def wait_for_both_tasks(
        tasks,
        *,
        return_when,
    ):
        del return_when
        await asyncio.gather(*tasks, return_exceptions=True)
        return set(tasks), set()

    monkeypatch.setattr(runtime_module.asyncio, "wait", wait_for_both_tasks)

    result = await make_runtime(
        streams,
        store,
        FakeTranscriber(error=transcription_cause),
        completer=completer,
        failure_handler=failure_handler,
        sleep=no_delay,
    ).process_next()

    assert isinstance(result, WorkerFailed)
    assert result.failure == ClassifiedTranscriptionFailure(
        category=TranscriptionFailureCategory.RETRYABLE,
        error_code="TRANSCRIPTION_UNEXPECTED_ERROR",
    )
    assert len(store.renew_calls) == 1
    assert len(failure_handler.calls) == 1
    assert completer.calls == []
    assert streams.ack_calls == [(GROUP_NAME, make_message())]


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
        (JOB_UUID, WORKER_ID, NOW + LEASE_DURATION, 2),
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
        ("heartbeat_interval", timedelta(0)),
        ("heartbeat_interval", LEASE_DURATION),
        ("heartbeat_interval", "60"),
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
        "completer": cast(TranscriptionCompleter, RecordingCompleter()),
        "failure_handler": cast(
            TranscriptionFailureHandler,
            RecordingFailureHandler(),
        ),
        "job_type": JobType.FAST,
        "group_name": GROUP_NAME,
        "worker_id": WORKER_ID,
        "lease_duration": LEASE_DURATION,
        "heartbeat_interval": timedelta(minutes=1),
    }
    arguments[parameter] = value

    with pytest.raises(ValueError, match=parameter):
        WorkerRuntime(**arguments)  # type: ignore[arg-type]
