from dataclasses import dataclass

import pytest

import dispatcher.application as application
from dispatcher.config import DispatcherSettings
from dispatcher.models import (
    DispatchBatchResult,
    DispatcherCycleResult,
    ReconciliationBatchResult,
    RecoveryBatchResult,
)
from transcribe_ai_shared import DatabaseSettings, RedisSettings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@dataclass
class FakeEngine:
    events: list[str]

    async def dispose(self) -> None:
        self.events.append("dispose_engine")


class EmptyRecoveryService:
    def __init__(self, **kwargs: object) -> None:
        pass

    async def recover_batch(
        self,
        batch_size: int,
        max_attempts: int,
    ) -> RecoveryBatchResult:
        return RecoveryBatchResult(0, 0, 0, 0, 0)


class EmptyReconciliationService:
    def __init__(self, **kwargs: object) -> None:
        pass

    async def reconcile_batch(
        self,
        batch_size: int,
        reconciliation_timeout_seconds: int,
    ) -> ReconciliationBatchResult:
        return ReconciliationBatchResult(0, 0, 0, 0)


async def test_run_dispatch_cycle_recovers_then_reconciles_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)
    session_factory = object()
    recovery_result = RecoveryBatchResult(2, 1, 1, 0, 0)
    reconciliation_result = ReconciliationBatchResult(3, 2, 1, 0)
    dispatch_result = DispatchBatchResult(2, 2, 2, 0, 0)
    captured: dict[str, object] = {}

    class FakeStreams:
        def __init__(self, redis_url: str) -> None:
            captured["redis_url"] = redis_url

        async def aclose(self) -> None:
            events.append("close_redis")

    class FakeStore:
        def __init__(self, active_session_factory: object) -> None:
            captured["session_factory"] = active_session_factory

    class FakeRecoveryService:
        def __init__(self, *, job_store: object) -> None:
            captured["recovery_store"] = job_store

        async def recover_batch(
            self,
            batch_size: int,
            max_attempts: int,
        ) -> RecoveryBatchResult:
            captured["recovery_batch_size"] = batch_size
            captured["max_attempts"] = max_attempts
            events.append("recovery")
            return recovery_result

    class FakeReconciliationService:
        def __init__(self, *, job_store: object) -> None:
            captured["reconciliation_store"] = job_store

        async def reconcile_batch(
            self,
            batch_size: int,
            reconciliation_timeout_seconds: int,
        ) -> ReconciliationBatchResult:
            captured["reconciliation_batch_size"] = batch_size
            captured["reconciliation_timeout_seconds"] = reconciliation_timeout_seconds
            events.append("reconciliation")
            return reconciliation_result

    class FakeDispatchService:
        def __init__(self, *, job_store: object, streams: object) -> None:
            captured["dispatch_store"] = job_store
            captured["streams"] = streams

        async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
            captured["dispatch_batch_size"] = batch_size
            events.append("dispatch")
            return dispatch_result

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
    monkeypatch.setattr(application, "LeaseRecoveryService", FakeRecoveryService)
    monkeypatch.setattr(
        application,
        "DispatchReconciliationService",
        FakeReconciliationService,
    )
    monkeypatch.setattr(application, "DispatcherService", FakeDispatchService)

    result = await application.run_dispatch_cycle(
        database_settings,
        redis_settings,
        DispatcherSettings(
            batch_size=25,
            max_attempts=5,
            reconciliation_timeout_seconds=600,
        ),
    )

    assert result == DispatcherCycleResult(
        recovery=recovery_result,
        reconciliation=reconciliation_result,
        dispatch=dispatch_result,
    )
    assert captured["redis_url"] == "redis://localhost:6379/0"
    assert captured["session_factory"] is session_factory
    assert captured["recovery_store"] is captured["dispatch_store"]
    assert captured["reconciliation_store"] is captured["dispatch_store"]
    assert captured["recovery_batch_size"] == 25
    assert captured["reconciliation_batch_size"] == 25
    assert captured["reconciliation_timeout_seconds"] == 600
    assert captured["dispatch_batch_size"] == 25
    assert captured["max_attempts"] == 5
    assert events == [
        "recovery",
        "reconciliation",
        "dispatch",
        "close_redis",
        "dispose_engine",
    ]


async def test_run_dispatch_cycle_closes_resources_when_dispatch_fails(
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
    monkeypatch.setattr(
        application,
        "LeaseRecoveryService",
        EmptyRecoveryService,
    )
    monkeypatch.setattr(
        application,
        "DispatchReconciliationService",
        EmptyReconciliationService,
    )
    monkeypatch.setattr(application, "DispatcherService", FailingService)

    with pytest.raises(RuntimeError, match="dispatch failed"):
        await application.run_dispatch_cycle(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            RedisSettings(redis_url="redis://localhost:6379/0"),
            DispatcherSettings(),
        )

    assert events == ["close_redis", "dispose_engine"]


async def test_run_dispatch_cycle_disposes_engine_when_redis_close_fails(
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
    monkeypatch.setattr(
        application,
        "LeaseRecoveryService",
        EmptyRecoveryService,
    )
    monkeypatch.setattr(
        application,
        "DispatchReconciliationService",
        EmptyReconciliationService,
    )
    monkeypatch.setattr(application, "DispatcherService", SuccessfulService)

    with pytest.raises(OSError, match="redis close failed"):
        await application.run_dispatch_cycle(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            RedisSettings(redis_url="redis://localhost:6379/0"),
            DispatcherSettings(),
        )

    assert events == ["close_redis", "dispose_engine"]


async def test_run_dispatch_cycle_does_not_dispatch_when_recovery_fails_globally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)

    class FakeStreams:
        def __init__(self, redis_url: str) -> None:
            pass

        async def aclose(self) -> None:
            events.append("close_redis")

    class FailingRecoveryService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def recover_batch(
            self,
            batch_size: int,
            max_attempts: int,
        ) -> RecoveryBatchResult:
            events.append("recovery")
            raise RuntimeError("database unavailable")

    class UnexpectedDispatchService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
            pytest.fail("dispatch must not run after a global recovery failure")

    class UnexpectedReconciliationService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def reconcile_batch(
            self,
            batch_size: int,
            reconciliation_timeout_seconds: int,
        ) -> ReconciliationBatchResult:
            pytest.fail("reconciliation must not run after a recovery failure")

    monkeypatch.setattr(application, "create_async_db_engine", lambda _: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda _: object(),
    )
    monkeypatch.setattr(application, "RedisTranscriptionStreams", FakeStreams)
    monkeypatch.setattr(application, "PostgresDispatchJobStore", lambda _: object())
    monkeypatch.setattr(
        application,
        "LeaseRecoveryService",
        FailingRecoveryService,
    )
    monkeypatch.setattr(
        application,
        "DispatchReconciliationService",
        UnexpectedReconciliationService,
    )
    monkeypatch.setattr(
        application,
        "DispatcherService",
        UnexpectedDispatchService,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await application.run_dispatch_cycle(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            RedisSettings(redis_url="redis://localhost:6379/0"),
            DispatcherSettings(),
        )

    assert events == ["recovery", "close_redis", "dispose_engine"]


async def test_run_dispatch_cycle_does_not_dispatch_after_reconciliation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    engine = FakeEngine(events)

    class FakeStreams:
        def __init__(self, redis_url: str) -> None:
            pass

        async def aclose(self) -> None:
            events.append("close_redis")

    class SuccessfulRecoveryService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def recover_batch(
            self,
            batch_size: int,
            max_attempts: int,
        ) -> RecoveryBatchResult:
            events.append("recovery")
            return RecoveryBatchResult(0, 0, 0, 0, 0)

    class FailingReconciliationService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def reconcile_batch(
            self,
            batch_size: int,
            reconciliation_timeout_seconds: int,
        ) -> ReconciliationBatchResult:
            events.append("reconciliation")
            raise RuntimeError("database unavailable")

    class UnexpectedDispatchService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def dispatch_batch(self, batch_size: int) -> DispatchBatchResult:
            pytest.fail("dispatch must not run after a reconciliation failure")

    monkeypatch.setattr(application, "create_async_db_engine", lambda _: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda _: object(),
    )
    monkeypatch.setattr(application, "RedisTranscriptionStreams", FakeStreams)
    monkeypatch.setattr(application, "PostgresDispatchJobStore", lambda _: object())
    monkeypatch.setattr(
        application,
        "LeaseRecoveryService",
        SuccessfulRecoveryService,
    )
    monkeypatch.setattr(
        application,
        "DispatchReconciliationService",
        FailingReconciliationService,
    )
    monkeypatch.setattr(
        application,
        "DispatcherService",
        UnexpectedDispatchService,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await application.run_dispatch_cycle(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            RedisSettings(redis_url="redis://localhost:6379/0"),
            DispatcherSettings(),
        )

    assert events == [
        "recovery",
        "reconciliation",
        "close_redis",
        "dispose_engine",
    ]
