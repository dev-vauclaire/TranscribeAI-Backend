from unittest.mock import Mock

import pytest
import redis

from transcribe_ai_shared.services.redis_queue_service import RedisQueueService


pytestmark = pytest.mark.unit


@pytest.fixture
def redis_client(monkeypatch):
    client = Mock()
    from_url = Mock(return_value=client)
    monkeypatch.setattr(
        "transcribe_ai_shared.services.redis_queue_service.redis.Redis.from_url",
        from_url,
    )
    return client, from_url


def test_configures_distinct_connection_and_blocking_timeouts(redis_client):
    _, from_url = redis_client

    RedisQueueService(
        "redis://localhost:6379/0",
        "jobs",
        pop_timeout_seconds=4.0,
        socket_timeout_seconds=9.0,
        socket_connect_timeout_seconds=3.0,
    )

    from_url.assert_called_once_with(
        "redis://localhost:6379/0",
        decode_responses=True,
        socket_timeout=9.0,
        socket_connect_timeout=3.0,
    )


def test_pop_job_returns_none_after_server_timeout(redis_client):
    client, _ = redis_client
    client.blpop.return_value = None
    service = RedisQueueService("redis://localhost:6379/0", "jobs")

    result = service.pop_job()

    assert result is None
    client.blpop.assert_called_once_with("jobs", timeout=5.0)


def test_pop_job_returns_job_uuid(redis_client):
    client, _ = redis_client
    client.blpop.return_value = ("jobs", "job-uuid")
    service = RedisQueueService("redis://localhost:6379/0", "jobs")

    result = service.pop_job()

    assert result == "job-uuid"


def test_healthcheck_translates_redis_error(redis_client):
    client, _ = redis_client
    redis_error = redis.exceptions.TimeoutError("Redis unavailable")
    client.ping.side_effect = redis_error
    service = RedisQueueService("redis://localhost:6379/0", "jobs")

    with pytest.raises(ConnectionError) as error:
        service.check_redis_connection()

    assert error.value.__cause__ is redis_error


def test_push_job_uses_configured_queue(redis_client):
    client, _ = redis_client
    service = RedisQueueService("redis://localhost:6379/0", "jobs")

    result = service.push_job("job-uuid")

    assert result == "job-uuid"
    client.rpush.assert_called_once_with("jobs", "job-uuid")


def test_get_queue_position_converts_index_to_one_based(redis_client):
    client, _ = redis_client
    client.execute_command.return_value = 2
    service = RedisQueueService("redis://localhost:6379/0", "jobs")

    result = service.get_queue_position("job-uuid")

    assert result == 3
    client.execute_command.assert_called_once_with("LPOS", "jobs", "job-uuid")


@pytest.mark.parametrize(
    ("pop_timeout", "socket_timeout"),
    [
        (0.0, 10.0),
        (5.0, 5.0),
        (5.0, 4.0),
    ],
)
def test_rejects_inconsistent_timeouts(
    redis_client,
    pop_timeout,
    socket_timeout,
):
    with pytest.raises(ValueError):
        RedisQueueService(
            "redis://localhost:6379/0",
            "jobs",
            pop_timeout_seconds=pop_timeout,
            socket_timeout_seconds=socket_timeout,
        )


def test_rejects_non_positive_connection_timeout(redis_client):
    with pytest.raises(ValueError):
        RedisQueueService(
            "redis://localhost:6379/0",
            "jobs",
            socket_connect_timeout_seconds=0.0,
        )
