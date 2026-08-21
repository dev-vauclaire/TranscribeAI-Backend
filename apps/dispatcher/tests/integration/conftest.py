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
def dispatcher_redis_container() -> Iterator[RedisContainer]:
    """Démarre la version Redis utilisée en production par le projet."""
    with RedisContainer("redis:8.10.1-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def dispatcher_redis_url(
    dispatcher_redis_container: RedisContainer,
) -> str:
    host = dispatcher_redis_container.get_container_host_ip()
    port = dispatcher_redis_container.get_exposed_port(REDIS_PORT)
    return f"redis://{host}:{port}/0"


@dataclass(frozen=True, slots=True)
class DispatcherRedisContext:
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


@pytest_asyncio.fixture
async def dispatcher_redis(
    dispatcher_redis_url: str,
) -> AsyncIterator[DispatcherRedisContext]:
    """Isole Redis et expose l'adaptateur ainsi qu'un client d'assertion."""
    client = redis_asyncio.Redis.from_url(
        dispatcher_redis_url,
        decode_responses=True,
    )
    streams = RedisTranscriptionStreams(dispatcher_redis_url)
    await client.flushdb()
    try:
        yield DispatcherRedisContext(streams=streams, client=client)
    finally:
        await client.flushdb()
        await streams.aclose()
        await client.aclose()


def _delete_transcription_rows(session_factory: SessionFactory) -> None:
    """Supprime les enfants avant les jobs pour respecter la clé étrangère."""
    with session_factory.begin() as session:
        session.execute(delete(TranscriptionResult))
        session.execute(delete(TranscriptionJob))


@pytest.fixture(autouse=True)
def clean_dispatcher_database(
    session_factory: SessionFactory,
) -> Iterator[None]:
    """Isole les scénarios sur le schéma construit par Alembic à la racine."""
    _delete_transcription_rows(session_factory)
    yield
    _delete_transcription_rows(session_factory)
