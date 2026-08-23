from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from redis import exceptions as redis_exceptions

from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.queue.exceptions import (
    RedisConnectionError,
    RedisOperationError,
)
from transcribe_ai_shared.queue.models import (
    AutoClaimResult,
    JobStreamMessage,
    ReceivedJobStreamMessage,
)
from transcribe_ai_shared.queue.redis_streams import RedisTranscriptionStreams


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

JOB_UUID = UUID("d3c31be4-e8de-4c01-8794-5444ce731c46")
REDIS_MESSAGE_ID = "1720000000000-0"


@pytest.fixture
def redis_streams(monkeypatch):
    client = Mock()
    for method_name in (
        "aclose",
        "xackdel",
        "xadd",
        "xautoclaim",
        "xgroup_create",
        "xreadgroup",
    ):
        setattr(client, method_name, AsyncMock())

    from_url = Mock(return_value=client)
    monkeypatch.setattr(
        "transcribe_ai_shared.queue.redis_streams.redis_asyncio.Redis.from_url",
        from_url,
    )
    streams = RedisTranscriptionStreams(
        "redis://localhost:6379/0",
        socket_timeout_seconds=9.0,
        socket_connect_timeout_seconds=3.0,
    )
    return streams, client, from_url


def make_message(job_type: JobType = JobType.FAST) -> JobStreamMessage:
    return JobStreamMessage(
        job_uuid=JOB_UUID,
        job_type=job_type,
        attempt_count=2,
    )


def make_received_message(
    job_type: JobType = JobType.FAST,
) -> ReceivedJobStreamMessage:
    return ReceivedJobStreamMessage(
        redis_message_id=REDIS_MESSAGE_ID,
        job_uuid=JOB_UUID,
        job_type=job_type,
        attempt_count=2,
    )


async def test_configures_async_redis_client(redis_streams):
    _, _, from_url = redis_streams

    from_url.assert_called_once_with(
        "redis://localhost:6379/0",
        decode_responses=False,
        socket_timeout=9.0,
        socket_connect_timeout=3.0,
    )


@pytest.mark.parametrize(
    ("job_type", "expected_stream"),
    [
        (JobType.FAST, "transcription:fast"),
        (
            JobType.LONG_FORM_DIARIZATION,
            "transcription:long-form-diarization",
        ),
    ],
)
async def test_publish_routes_and_serializes_job(
    redis_streams,
    job_type: JobType,
    expected_stream: str,
):
    streams, client, _ = redis_streams
    client.xadd.return_value = REDIS_MESSAGE_ID.encode()

    result = await streams.publish(make_message(job_type))

    assert result == REDIS_MESSAGE_ID
    client.xadd.assert_awaited_once_with(
        expected_stream,
        {
            "job_uuid": str(JOB_UUID),
            "attempt_count": "2",
        },
    )


async def test_ensure_consumer_group_starts_at_origin_and_creates_stream(
    redis_streams,
):
    streams, client, _ = redis_streams

    await streams.ensure_consumer_group(JobType.FAST, "workers")

    client.xgroup_create.assert_awaited_once_with(
        "transcription:fast",
        "workers",
        id="0-0",
        mkstream=True,
    )


async def test_ensure_consumer_group_is_idempotent_for_busygroup(redis_streams):
    streams, client, _ = redis_streams
    client.xgroup_create.side_effect = redis_exceptions.ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )

    await streams.ensure_consumer_group(JobType.FAST, "workers")


async def test_ensure_consumer_group_does_not_hide_other_server_errors(
    redis_streams,
):
    streams, client, _ = redis_streams
    redis_error = redis_exceptions.ResponseError("WRONGTYPE operation")
    client.xgroup_create.side_effect = redis_error

    with pytest.raises(RedisOperationError) as error:
        await streams.ensure_consumer_group(JobType.FAST, "workers")

    assert error.value.__cause__ is redis_error


async def test_consume_reads_one_new_message_and_exposes_redis_id(redis_streams):
    streams, client, _ = redis_streams
    client.xreadgroup.return_value = [
        [
            "transcription:fast",
            [
                [
                    REDIS_MESSAGE_ID,
                    {
                        "job_uuid": str(JOB_UUID),
                        "attempt_count": "2",
                    },
                ]
            ],
        ]
    ]

    result = await streams.consume(
        JobType.FAST,
        "workers",
        "worker-a",
        block_milliseconds=1_500,
    )

    assert result == make_received_message()
    client.xreadgroup.assert_awaited_once_with(
        groupname="workers",
        consumername="worker-a",
        streams={"transcription:fast": ">"},
        count=1,
        block=1_500,
        noack=False,
    )


async def test_consume_returns_none_when_no_new_message(redis_streams):
    streams, client, _ = redis_streams
    client.xreadgroup.return_value = []

    result = await streams.consume(
        JobType.LONG_FORM_DIARIZATION,
        "workers",
        "worker-a",
    )

    assert result is None


@pytest.mark.parametrize(("native_result", "expected"), [([1], True), ([-1], False)])
async def test_ack_and_delete_uses_xackdel(
    redis_streams,
    native_result,
    expected: bool,
):
    streams, client, _ = redis_streams
    client.xackdel.return_value = native_result

    result = await streams.ack_and_delete("workers", make_received_message())

    assert result is expected
    client.xackdel.assert_awaited_once_with(
        "transcription:fast",
        "workers",
        REDIS_MESSAGE_ID,
        ref_policy="KEEPREF",
    )


async def test_ack_and_delete_translates_server_error(redis_streams):
    streams, client, _ = redis_streams
    redis_error = redis_exceptions.ResponseError("unknown command 'XACKDEL'")
    client.xackdel.side_effect = redis_error

    with pytest.raises(RedisOperationError) as error:
        await streams.ack_and_delete("workers", make_received_message())

    assert error.value.__cause__ is redis_error


@pytest.mark.parametrize(
    "unexpected_result",
    [[], [0], [2], [True], 1, [[1]]],
)
async def test_ack_and_delete_rejects_unexpected_server_response(
    redis_streams,
    unexpected_result,
):
    streams, client, _ = redis_streams
    client.xackdel.return_value = unexpected_result

    with pytest.raises(RedisOperationError, match="Réponse XACKDEL inattendue"):
        await streams.ack_and_delete("workers", make_received_message())


async def test_autoclaim_normalizes_messages_cursor_and_deleted_ids(redis_streams):
    streams, client, _ = redis_streams
    client.xautoclaim.return_value = [
        b"1720000000100-0",
        [
            [
                REDIS_MESSAGE_ID.encode(),
                {
                    b"job_uuid": str(JOB_UUID).encode(),
                    b"attempt_count": b"2",
                },
            ]
        ],
        [b"1719999999999-0"],
    ]

    result = await streams.autoclaim(
        JobType.FAST,
        "workers",
        "worker-b",
        min_idle_milliseconds=30_000,
        start_id="0-0",
        count=10,
    )

    assert result == AutoClaimResult(
        next_start_id="1720000000100-0",
        messages=(make_received_message(),),
        deleted_message_ids=("1719999999999-0",),
    )
    client.xautoclaim.assert_awaited_once_with(
        name="transcription:fast",
        groupname="workers",
        consumername="worker-b",
        min_idle_time=30_000,
        start_id="0-0",
        count=10,
        justid=False,
    )


async def test_autoclaim_accepts_two_part_response_from_older_client(
    redis_streams,
):
    streams, client, _ = redis_streams
    client.xautoclaim.return_value = ["0-0", []]

    result = await streams.autoclaim(
        JobType.LONG_FORM_DIARIZATION,
        "workers",
        "worker-b",
        min_idle_milliseconds=0,
    )

    assert result == AutoClaimResult(
        next_start_id="0-0",
        messages=(),
        deleted_message_ids=(),
    )


@pytest.mark.parametrize(
    "redis_error",
    [
        redis_exceptions.ConnectionError("connection unavailable"),
        redis_exceptions.TimeoutError("timeout"),
    ],
)
async def test_translates_connection_failures(redis_streams, redis_error):
    streams, client, _ = redis_streams
    client.xadd.side_effect = redis_error

    with pytest.raises(RedisConnectionError) as error:
        await streams.publish(make_message())

    assert error.value.__cause__ is redis_error


async def test_translates_other_redis_failures(redis_streams):
    streams, client, _ = redis_streams
    redis_error = redis_exceptions.ResponseError("ERR command failed")
    client.xreadgroup.side_effect = redis_error

    with pytest.raises(RedisOperationError) as error:
        await streams.consume(JobType.FAST, "workers", "worker-a")

    assert error.value.__cause__ is redis_error


@pytest.mark.parametrize(
    ("method_name", "arguments"),
    [
        ("ensure_consumer_group", (JobType.FAST, "")),
        ("consume", (JobType.FAST, "workers", "  ")),
    ],
)
async def test_rejects_empty_group_or_consumer_names(
    redis_streams,
    method_name: str,
    arguments: tuple,
):
    streams, _, _ = redis_streams

    with pytest.raises(ValueError):
        await getattr(streams, method_name)(*arguments)


@pytest.mark.parametrize("block_milliseconds", [0, -1, True])
async def test_consume_rejects_invalid_block_timeout(
    redis_streams,
    block_milliseconds,
):
    streams, client, _ = redis_streams

    with pytest.raises(ValueError):
        await streams.consume(
            JobType.FAST,
            "workers",
            "worker-a",
            block_milliseconds=block_milliseconds,
        )

    client.xreadgroup.assert_not_awaited()


async def test_consume_rejects_block_timeout_reaching_socket_timeout(
    redis_streams,
):
    streams, client, _ = redis_streams

    with pytest.raises(ValueError, match="timeout socket"):
        await streams.consume(
            JobType.FAST,
            "workers",
            "worker-a",
            block_milliseconds=9_000,
        )

    client.xreadgroup.assert_not_awaited()


@pytest.mark.parametrize(
    ("min_idle_milliseconds", "count"),
    [(-1, 1), (True, 1), (0, 0), (0, True)],
)
async def test_autoclaim_rejects_invalid_numbers(
    redis_streams,
    min_idle_milliseconds,
    count,
):
    streams, client, _ = redis_streams

    with pytest.raises(ValueError):
        await streams.autoclaim(
            JobType.FAST,
            "workers",
            "worker-a",
            min_idle_milliseconds=min_idle_milliseconds,
            count=count,
        )

    client.xautoclaim.assert_not_awaited()


async def test_aclose_closes_client(redis_streams):
    streams, client, _ = redis_streams

    await streams.aclose()

    client.aclose.assert_awaited_once_with()
