from collections.abc import Iterator
from uuid import uuid4

import pytest
from testcontainers.redis import RedisContainer

from transcribe_ai_shared.services.redis_queue_service import RedisQueueService


REDIS_PORT = 6379


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(REDIS_PORT)

    return f"redis://{host}:{port}/0"


@pytest.fixture
def redis_queue_service(redis_url: str) -> Iterator[RedisQueueService]:
    queue_name = f"test:jobs:{uuid4().hex}"
    service = RedisQueueService(redis_url, queue_name)

    try:
        yield service
    finally:
        service.redis.delete(queue_name)
        service.redis.close()
