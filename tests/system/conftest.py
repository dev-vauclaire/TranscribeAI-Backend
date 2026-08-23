from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from api.config import ApiSettings
from api.create_app import create_app
from transcribe_ai_shared import (
    FileSystemAudioStorage,
    RedisTranscriptionStreams,
    TranscriptionJob,
    TranscriptionResult,
)


REDIS_PORT = 6379


@pytest.fixture(scope="session")
def system_redis_container() -> Iterator[RedisContainer]:
    """Démarre la version Redis ciblée par les applications de production."""
    with RedisContainer("redis:8.10.1-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def system_redis_url(system_redis_container: RedisContainer) -> str:
    host = system_redis_container.get_container_host_ip()
    port = system_redis_container.get_exposed_port(REDIS_PORT)
    return f"redis://{host}:{port}/0"


@dataclass(frozen=True, slots=True)
class SystemRedisContext:
    url: str
    streams: RedisTranscriptionStreams
    client: redis_asyncio.Redis


@pytest_asyncio.fixture
async def system_redis(
    system_redis_url: str,
) -> AsyncIterator[SystemRedisContext]:
    """Isole Redis tout en partageant les vraies primitives entre les composants."""
    client = redis_asyncio.Redis.from_url(
        system_redis_url,
        decode_responses=True,
    )
    streams = RedisTranscriptionStreams(system_redis_url)
    try:
        await client.flushdb()
        try:
            yield SystemRedisContext(
                url=system_redis_url,
                streams=streams,
                client=client,
            )
        finally:
            await client.flushdb()
    finally:
        try:
            await streams.aclose()
        finally:
            await client.aclose()


async def _delete_transcription_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory.begin() as session:
        await session.execute(delete(TranscriptionResult))
        await session.execute(delete(TranscriptionJob))


@pytest_asyncio.fixture(autouse=True)
async def clean_system_database(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Isole chaque workflow sur le schéma réellement construit par Alembic."""
    await _delete_transcription_rows(async_session_factory)
    yield
    await _delete_transcription_rows(async_session_factory)


@pytest.fixture
def system_audio_storage(tmp_path: Path) -> FileSystemAudioStorage:
    return FileSystemAudioStorage(tmp_path / "transcriptions")


@pytest_asyncio.fixture
async def system_api_client(
    system_audio_storage: FileSystemAudioStorage,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """Expose l'API réelle avec ses frontières PostgreSQL et filesystem de test."""
    app = create_app(
        settings=ApiSettings(
            max_upload_size_bytes=1024 * 1024,
            ffprobe_path="ffprobe",
            ffprobe_timeout_seconds=5,
            fast_max_duration_seconds=Decimal("60"),
            batch_max_duration_seconds=Decimal("3600"),
        ),
        storage=system_audio_storage,
        session_factory=async_session_factory,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client
