from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from sqlalchemy import delete
from testcontainers.redis import RedisContainer

from transcribe_ai_shared import (
    RedisTranscriptionStreams,
    SessionFactory,
    TranscriptionJob,
    TranscriptionResult,
)


REDIS_PORT = 6379


@pytest.fixture(scope="session")
def worker_redis_container() -> Iterator[RedisContainer]:
    """Démarre la même version Redis que celle ciblée en production."""
    with RedisContainer("redis:8.10.1-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def worker_redis_url(worker_redis_container: RedisContainer) -> str:
    host = worker_redis_container.get_container_host_ip()
    port = worker_redis_container.get_exposed_port(REDIS_PORT)
    return f"redis://{host}:{port}/0"


@dataclass(frozen=True, slots=True)
class WorkerRedisContext:
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


@pytest_asyncio.fixture
async def worker_redis(
    worker_redis_url: str,
) -> AsyncIterator[WorkerRedisContext]:
    """Isole Redis et expose un client réservé aux assertions externes."""
    client = redis_asyncio.Redis.from_url(
        worker_redis_url,
        decode_responses=True,
    )
    streams = RedisTranscriptionStreams(worker_redis_url)
    await client.flushdb()
    try:
        yield WorkerRedisContext(streams=streams, client=client)
    finally:
        await client.flushdb()
        await streams.aclose()
        await client.aclose()


def _delete_transcription_rows(session_factory: SessionFactory) -> None:
    """Supprime les résultats avant les jobs pour respecter leur clé étrangère."""
    with session_factory.begin() as session:
        session.execute(delete(TranscriptionResult))
        session.execute(delete(TranscriptionJob))


@pytest.fixture(autouse=True)
def clean_worker_database(
    session_factory: SessionFactory,
) -> Iterator[None]:
    """Isole chaque scénario sur le schéma PostgreSQL construit par Alembic."""
    _delete_transcription_rows(session_factory)
    yield
    _delete_transcription_rows(session_factory)
