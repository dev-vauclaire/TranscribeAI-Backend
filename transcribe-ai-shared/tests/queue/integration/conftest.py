from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from testcontainers.redis import RedisContainer

from transcribe_ai_shared.queue.redis_streams import RedisTranscriptionStreams


REDIS_PORT = 6379


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:8.10.1-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(REDIS_PORT)

    return f"redis://{host}:{port}/0"


@dataclass(frozen=True, slots=True)
class RedisStreamsTestContext:
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


@pytest_asyncio.fixture
async def redis_streams_context(
    redis_url: str,
) -> AsyncIterator[RedisStreamsTestContext]:
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    streams = RedisTranscriptionStreams(redis_url)
    await client.flushdb()
    try:
        yield RedisStreamsTestContext(streams=streams, client=client)
    finally:
        await client.flushdb()
        await streams.aclose()
        await client.aclose()
