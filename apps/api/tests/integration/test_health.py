from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import ApiSettings
from api.create_app import create_app
from transcribe_ai_shared import (
    DatabaseSettings,
    FileSystemAudioStorage,
    create_async_db_engine,
    create_async_session_factory,
)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@asynccontextmanager
async def _client_for(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


async def test_ready_returns_200_when_postgres_is_available(
    tmp_path: Path,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = create_app(
        settings=ApiSettings(readiness_timeout_seconds=1),
        storage=FileSystemAudioStorage(tmp_path),
        session_factory=async_session_factory,
    )

    async with _client_for(app) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_live_stays_available_and_ready_returns_503_when_postgres_is_down(
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    engine = create_async_db_engine(
        DatabaseSettings(
            url=(
                "postgresql://postgres:postgres@127.0.0.1:"
                f"{unused_tcp_port}/transcribe_ai_healthcheck"
            ),
            pool_timeout_seconds=0.1,
        )
    )
    session_factory = create_async_session_factory(engine)
    app = create_app(
        settings=ApiSettings(readiness_timeout_seconds=0.2),
        storage=FileSystemAudioStorage(tmp_path),
        session_factory=session_factory,
    )

    try:
        async with _client_for(app) as client:
            live_response = await client.get("/health/live")
            ready_response = await client.get("/health/ready")
    finally:
        await engine.dispose()

    assert live_response.status_code == 200
    assert live_response.json() == {"status": "alive"}
    assert ready_response.status_code == 503
    assert ready_response.json() == {"status": "not_ready"}
