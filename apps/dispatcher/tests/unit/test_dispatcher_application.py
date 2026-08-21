from dataclasses import dataclass

import pytest

import dispatcher.application as application
from dispatcher.config import DispatcherSettings
from dispatcher.models import DispatchBatchResult
from transcribe_ai_shared import DatabaseSettings, RedisSettings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@dataclass
class FakeEngine:
    events: list[str]

    async def dispose(self) -> None:
        self.events.append("dispose_engine")


async def test_run_dispatch_batch_composes_dependencies_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)
    session_factory = object()
    expected_result = DispatchBatchResult(2, 2, 2, 0, 0)
    captured: dict[str, object] = {}

    class FakeStreams:
        def __init__(self, redis_url: str) -> None:
            captured["redis_url"] = redis_url

        async def aclose(self) -> None:
            events.append("close_redis")

    class FakeStore:
        def __init__(self, active_session_factory: object) -> None:
            captured["session_factory"] = active_session_factory

    class FakeService:
        def __init__(self, *, job_store: object, streams: object) -> None:
            captured["job_store"] = job_store
            captured["streams"] = streams

        async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
            captured["batch_size"] = batch_size
            events.append("dispatch")
            return expected_result

    database_settings = DatabaseSettings(
        url="postgresql://postgres:postgres@localhost/postgres"
    )
    redis_settings = RedisSettings(redis_url="redis://localhost:6379/0")

    monkeypatch.setattr(
        application,
        "create_async_db_engine",
        lambda settings: engine,
    )
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda active_engine: session_factory,
    )
    monkeypatch.setattr(application, "RedisTranscriptionStreams", FakeStreams)
    monkeypatch.setattr(application, "PostgresDispatchJobStore", FakeStore)
    monkeypatch.setattr(application, "DispatcherService", FakeService)

    result = await application.run_dispatch_batch(
        database_settings,
        redis_settings,
        DispatcherSettings(batch_size=25),
    )

    assert result is expected_result
    assert captured["redis_url"] == "redis://localhost:6379/0"
    assert captured["session_factory"] is session_factory
    assert captured["batch_size"] == 25
    assert events == ["dispatch", "close_redis", "dispose_engine"]


async def test_run_dispatch_batch_closes_resources_when_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)

    class FakeStreams:
        def __init__(self, redis_url: str) -> None:
            pass

        async def aclose(self) -> None:
            events.append("close_redis")

    class FailingService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
            raise RuntimeError("dispatch failed")

    monkeypatch.setattr(application, "create_async_db_engine", lambda _: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda _: object(),
    )
    monkeypatch.setattr(application, "RedisTranscriptionStreams", FakeStreams)
    monkeypatch.setattr(application, "PostgresDispatchJobStore", lambda _: object())
    monkeypatch.setattr(application, "DispatcherService", FailingService)

    with pytest.raises(RuntimeError, match="dispatch failed"):
        await application.run_dispatch_batch(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            RedisSettings(redis_url="redis://localhost:6379/0"),
            DispatcherSettings(),
        )

    assert events == ["close_redis", "dispose_engine"]


async def test_run_dispatch_batch_disposes_engine_when_redis_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)

    class FailingCloseStreams:
        def __init__(self, redis_url: str) -> None:
            pass

        async def aclose(self) -> None:
            events.append("close_redis")
            raise OSError("redis close failed")

    class SuccessfulService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
            return DispatchBatchResult(0, 0, 0, 0, 0)

    monkeypatch.setattr(application, "create_async_db_engine", lambda _: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda _: object(),
    )
    monkeypatch.setattr(
        application,
        "RedisTranscriptionStreams",
        FailingCloseStreams,
    )
    monkeypatch.setattr(application, "PostgresDispatchJobStore", lambda _: object())
    monkeypatch.setattr(application, "DispatcherService", SuccessfulService)

    with pytest.raises(OSError, match="redis close failed"):
        await application.run_dispatch_batch(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            RedisSettings(redis_url="redis://localhost:6379/0"),
            DispatcherSettings(),
        )

    assert events == ["close_redis", "dispose_engine"]
