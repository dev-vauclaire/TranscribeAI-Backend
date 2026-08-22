from dataclasses import dataclass
from datetime import timedelta

import pytest

import transcribe_ai_shared.worker.application as application
from transcribe_ai_shared import (
    DatabaseSettings,
    JobType,
    RedisSettings,
    WorkerSettings,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@dataclass
class FakeEngine:
    events: list[str]

    async def dispose(self) -> None:
        self.events.append("dispose_engine")


async def test_run_worker_composes_runtime_and_keeps_resources_between_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, object] = {}
    engine = FakeEngine(events)
    session_factory = object()
    transcriber = object()
    expected_result = object()

    class StopWorker(Exception):
        pass

    class FakeStreams:
        def __init__(self, redis_url: str) -> None:
            captured["redis_url"] = redis_url

        async def aclose(self) -> None:
            events.append("close_redis")

    class FakeStore:
        def __init__(
            self,
            active_session_factory: object,
            *,
            expected_job_type: JobType,
        ) -> None:
            captured["session_factory"] = active_session_factory
            captured["store_job_type"] = expected_job_type

    class FakeCompleter:
        def __init__(self, active_session_factory: object) -> None:
            captured["completion_session_factory"] = active_session_factory

    class FakeRuntime:
        def __init__(self, **dependencies: object) -> None:
            captured.update(dependencies)

        async def initialize(self) -> None:
            events.append("initialize")

        async def process_next(self, *, block_milliseconds: int) -> object:
            captured["block_milliseconds"] = block_milliseconds
            events.append("process_next")
            return expected_result

    def stop_after_first_result(result: object) -> None:
        captured["result"] = result
        events.append("on_result")
        raise StopWorker

    monkeypatch.setattr(application, "create_async_db_engine", lambda _: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda _: session_factory,
    )
    monkeypatch.setattr(application, "RedisTranscriptionStreams", FakeStreams)
    monkeypatch.setattr(application, "PostgresWorkerJobStore", FakeStore)
    monkeypatch.setattr(
        application,
        "TranscriptionCompletionService",
        FakeCompleter,
    )
    monkeypatch.setattr(application, "WorkerRuntime", FakeRuntime)

    with pytest.raises(StopWorker):
        await application.run_worker(
            database_settings=DatabaseSettings(
                url="postgresql://postgres:postgres@localhost/postgres"
            ),
            redis_settings=RedisSettings(redis_url="redis://localhost:6379/0"),
            worker_settings=WorkerSettings(
                worker_id="batch-1",
                worker_consumer_group="workers",
                worker_block_milliseconds=250,
                worker_lease_seconds=90,
            ),
            transcriber=transcriber,
            job_type=JobType.BATCH,
            on_result=stop_after_first_result,
        )

    assert captured["result"] is expected_result
    assert captured["redis_url"] == "redis://localhost:6379/0"
    assert captured["session_factory"] is session_factory
    assert captured["store_job_type"] is JobType.BATCH
    assert captured["transcriber"] is transcriber
    assert captured["completion_session_factory"] is session_factory
    assert isinstance(captured["completer"], FakeCompleter)
    assert captured["job_type"] is JobType.BATCH
    assert captured["group_name"] == "workers"
    assert captured["worker_id"] == "batch-1"
    assert captured["lease_duration"] == timedelta(seconds=90)
    assert captured["heartbeat_interval"] == timedelta(seconds=60)
    assert captured["block_milliseconds"] == 250
    assert events == [
        "initialize",
        "process_next",
        "on_result",
        "close_redis",
        "dispose_engine",
    ]


async def test_run_worker_closes_resources_when_runtime_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)

    class FakeStreams:
        def __init__(self, _redis_url: str) -> None:
            pass

        async def aclose(self) -> None:
            events.append("close_redis")

    class FailingRuntime:
        def __init__(self, **_dependencies: object) -> None:
            pass

        async def initialize(self) -> None:
            pass

        async def process_next(self, *, block_milliseconds: int) -> object:
            raise RuntimeError("runtime failed")

    monkeypatch.setattr(application, "create_async_db_engine", lambda _: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda _: object(),
    )
    monkeypatch.setattr(application, "RedisTranscriptionStreams", FakeStreams)
    monkeypatch.setattr(
        application,
        "PostgresWorkerJobStore",
        lambda _, **_kwargs: object(),
    )
    monkeypatch.setattr(application, "WorkerRuntime", FailingRuntime)

    with pytest.raises(RuntimeError, match="runtime failed"):
        await application.run_worker(
            database_settings=DatabaseSettings(
                url="postgresql://postgres:postgres@localhost/postgres"
            ),
            redis_settings=RedisSettings(redis_url="redis://localhost:6379/0"),
            worker_settings=WorkerSettings(worker_id="fast-1"),
            transcriber=object(),
            job_type=JobType.FAST,
        )

    assert events == ["close_redis", "dispose_engine"]
