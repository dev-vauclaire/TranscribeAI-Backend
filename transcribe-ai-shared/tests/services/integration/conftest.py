import os
from uuid import uuid4

import pytest
import redis

from transcribe_ai_shared.services.redis_queue_service import RedisQueueService


TEST_REDIS_URL_ENV = "TEST_REDIS_URL"


@pytest.fixture
def redis_queue_service():
    redis_url = os.getenv(TEST_REDIS_URL_ENV)
    if not redis_url:
        pytest.fail(
            f"{TEST_REDIS_URL_ENV} must contain the URL of a dedicated "
            "Redis test instance"
        )

    queue_name = f"transcribe_ai_test_{uuid4().hex}"
    cleanup_client = redis.Redis.from_url(redis_url, decode_responses=True)

    try:
        cleanup_client.ping()
    except redis.RedisError as error:
        cleanup_client.close()
        pytest.fail(f"Cannot connect to {TEST_REDIS_URL_ENV}: {error}")

    service = RedisQueueService(redis_url, queue_name)

    try:
        yield service
    finally:
        cleanup_client.delete(queue_name)
        service.redis.close()
        cleanup_client.close()
