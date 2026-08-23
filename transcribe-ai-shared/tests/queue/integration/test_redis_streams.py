from uuid import UUID

import pytest

from transcribe_ai_shared.database.models.enums import JobType
from transcribe_ai_shared.queue.models import JobStreamMessage

from .conftest import RedisStreamsTestContext


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FAST_STREAM = "transcription:fast"
LONG_FORM_DIARIZATION_STREAM = "transcription:long-form-diarization"
GROUP_NAME = "transcription-workers"
JOB_UUID = UUID("12345678-1234-5678-1234-567812345678")
SECOND_JOB_UUID = UUID("87654321-4321-8765-4321-876543218765")


def make_message(
    job_type: JobType = JobType.FAST,
    *,
    job_uuid: UUID = JOB_UUID,
    attempt_count: int = 2,
) -> JobStreamMessage:
    return JobStreamMessage(
        job_uuid=job_uuid,
        job_type=job_type,
        attempt_count=attempt_count,
    )


async def test_publish_adds_the_exact_job_payload_to_the_expected_stream(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    message = make_message()

    redis_message_id = await redis_streams_context.streams.publish(message)

    assert await redis_streams_context.client.xrange(FAST_STREAM) == [
        (
            redis_message_id,
            {
                "job_uuid": str(JOB_UUID),
                "attempt_count": "2",
            },
        )
    ]
    assert await redis_streams_context.client.exists(LONG_FORM_DIARIZATION_STREAM) == 0


async def test_ensure_consumer_group_is_idempotent_and_creates_the_stream(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams

    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)

    assert await redis_streams_context.client.exists(FAST_STREAM) == 1
    groups = await redis_streams_context.client.xinfo_groups(FAST_STREAM)
    assert [group["name"] for group in groups] == [GROUP_NAME]
    assert groups[0]["last-delivered-id"] == "0-0"


async def test_group_created_after_publication_consumes_the_existing_message(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams
    redis_message_id = await streams.publish(make_message())

    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    received = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "worker-a",
        block_milliseconds=100,
    )

    assert received is not None
    assert received.redis_message_id == redis_message_id


async def test_consume_exposes_the_redis_id_and_places_the_message_in_the_pel(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams
    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    redis_message_id = await streams.publish(make_message(attempt_count=3))

    received = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "worker-a",
        block_milliseconds=100,
    )

    assert received is not None
    assert received.redis_message_id == redis_message_id
    assert received.job_uuid == JOB_UUID
    assert received.job_type is JobType.FAST
    assert received.attempt_count == 3
    pending = await redis_streams_context.client.xpending(FAST_STREAM, GROUP_NAME)
    assert pending["pending"] == 1
    assert pending["min"] == redis_message_id
    assert pending["max"] == redis_message_id
    assert pending["consumers"] == [{"name": "worker-a", "pending": 1}]


async def test_ack_and_delete_removes_the_pending_entry_and_stream_message(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams
    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    await streams.publish(make_message())
    received = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "worker-a",
        block_milliseconds=100,
    )
    assert received is not None

    first_result = await streams.ack_and_delete(GROUP_NAME, received)
    second_result = await streams.ack_and_delete(GROUP_NAME, received)

    assert first_result is True
    assert second_result is False
    pending = await redis_streams_context.client.xpending(FAST_STREAM, GROUP_NAME)
    assert pending["pending"] == 0
    assert await redis_streams_context.client.xrange(FAST_STREAM) == []


async def test_fast_and_long_form_diarization_jobs_remain_isolated(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams
    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    await streams.ensure_consumer_group(
        JobType.LONG_FORM_DIARIZATION,
        GROUP_NAME,
    )
    fast_redis_id = await streams.publish(make_message(JobType.FAST))
    long_form_diarization_redis_id = await streams.publish(
        make_message(
            JobType.LONG_FORM_DIARIZATION,
            job_uuid=SECOND_JOB_UUID,
            attempt_count=4,
        )
    )

    fast_message = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "fast-worker",
        block_milliseconds=100,
    )
    long_form_diarization_message = await streams.consume(
        JobType.LONG_FORM_DIARIZATION,
        GROUP_NAME,
        "long-form-diarization-worker",
        block_milliseconds=100,
    )

    assert fast_message is not None
    assert fast_message.redis_message_id == fast_redis_id
    assert fast_message.job_uuid == JOB_UUID
    assert fast_message.job_type is JobType.FAST
    assert long_form_diarization_message is not None
    assert (
        long_form_diarization_message.redis_message_id == long_form_diarization_redis_id
    )
    assert long_form_diarization_message.job_uuid == SECOND_JOB_UUID
    assert long_form_diarization_message.job_type is JobType.LONG_FORM_DIARIZATION


async def test_two_consumers_in_one_group_receive_distinct_new_messages(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams
    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    first_redis_id = await streams.publish(make_message())
    second_redis_id = await streams.publish(
        make_message(job_uuid=SECOND_JOB_UUID, attempt_count=1)
    )

    first_received = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "worker-a",
        block_milliseconds=100,
    )
    second_received = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "worker-b",
        block_milliseconds=100,
    )

    assert first_received is not None
    assert second_received is not None
    assert {
        first_received.redis_message_id,
        second_received.redis_message_id,
    } == {first_redis_id, second_redis_id}
    pending = await redis_streams_context.client.xpending(FAST_STREAM, GROUP_NAME)
    assert pending["pending"] == 2
    assert {
        consumer["name"]: consumer["pending"] for consumer in pending["consumers"]
    } == {"worker-a": 1, "worker-b": 1}


async def test_autoclaim_transfers_pending_ownership_without_acknowledging(
    redis_streams_context: RedisStreamsTestContext,
) -> None:
    streams = redis_streams_context.streams
    await streams.ensure_consumer_group(JobType.FAST, GROUP_NAME)
    redis_message_id = await streams.publish(make_message())
    received = await streams.consume(
        JobType.FAST,
        GROUP_NAME,
        "worker-a",
        block_milliseconds=100,
    )
    assert received is not None

    claim_result = await streams.autoclaim(
        JobType.FAST,
        GROUP_NAME,
        "worker-b",
        min_idle_milliseconds=0,
    )

    assert claim_result.next_start_id == "0-0"
    assert claim_result.deleted_message_ids == ()
    assert claim_result.messages == (received,)
    pending_entries = await redis_streams_context.client.xpending_range(
        FAST_STREAM,
        GROUP_NAME,
        min="-",
        max="+",
        count=10,
    )
    assert pending_entries == [
        {
            "message_id": redis_message_id,
            "consumer": "worker-b",
            "time_since_delivered": pending_entries[0]["time_since_delivered"],
            "times_delivered": 2,
        }
    ]
    assert await redis_streams_context.client.xrange(FAST_STREAM) != []
