import asyncio
import logging
from dataclasses import dataclass, field
from unittest.mock import Mock

import pytest

import transcribe_ai_shared.worker_healthcheck as healthcheck_module
from transcribe_ai_shared import DatabaseSettings, RedisSettings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def preserve_test_logging_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> Mock:
    configure_logging = Mock()
    monkeypatch.setattr(
        healthcheck_module,
        "configure_logging",
        configure_logging,
    )
    return configure_logging


@dataclass
class FakeConnection:
    statements: list[str] = field(default_factory=list)
    error: Exception | None = None
    wait_forever: asyncio.Event | None = None

    async def execute(self, statement: object) -> None:
        self.statements.append(str(statement))
        if self.error is not None:
            raise self.error
        if self.wait_forever is not None:
            await self.wait_forever.wait()


@dataclass
class FakeConnectionContext:
    connection: FakeConnection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


@dataclass
class FakeEngine:
    connection: FakeConnection
    dispose_count: int = 0

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(self.connection)

    async def dispose(self) -> None:
        self.dispose_count += 1


@dataclass
class FakeRedis:
    ping_result: bool = True
    error: Exception | None = None
    ping_count: int = 0
    close_count: int = 0

    async def ping(self) -> bool:
        self.ping_count += 1
        if self.error is not None:
            raise self.error
        return self.ping_result

    async def aclose(self) -> None:
        self.close_count += 1


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    engine: FakeEngine,
    redis_client: FakeRedis,
) -> tuple[Mock, Mock]:
    engine_factory = Mock(return_value=engine)
    redis_factory = Mock(return_value=redis_client)
    monkeypatch.setattr(
        healthcheck_module,
        "create_async_db_engine",
        engine_factory,
    )
    monkeypatch.setattr(
        healthcheck_module,
        "_create_redis_client",
        redis_factory,
    )
    return engine_factory, redis_factory


def make_settings() -> tuple[DatabaseSettings, RedisSettings]:
    return (
        DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
        RedisSettings(redis_url="redis://localhost:6379/0"),
    )


@pytest.mark.asyncio
async def test_worker_dependencies_healthcheck_probes_postgres_then_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection()
    engine = FakeEngine(connection)
    redis_client = FakeRedis()
    engine_factory, redis_factory = install_fakes(
        monkeypatch,
        engine,
        redis_client,
    )
    database_settings, redis_settings = make_settings()

    await healthcheck_module.check_worker_dependencies(
        database_settings,
        redis_settings,
        timeout_seconds=2.5,
    )

    engine_factory.assert_called_once_with(database_settings)
    redis_factory.assert_called_once_with(redis_settings, 2.5)
    assert connection.statements == ["SELECT 1"]
    assert redis_client.ping_count == 1
    assert redis_client.close_count == 1
    assert engine.dispose_count == 1


@pytest.mark.asyncio
async def test_worker_dependencies_healthcheck_closes_resources_when_postgres_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine(FakeConnection(error=OSError("database unavailable")))
    redis_client = FakeRedis()
    install_fakes(monkeypatch, engine, redis_client)

    with pytest.raises(OSError, match="database unavailable"):
        await healthcheck_module.check_worker_dependencies(*make_settings())

    assert redis_client.ping_count == 0
    assert redis_client.close_count == 1
    assert engine.dispose_count == 1


@pytest.mark.asyncio
async def test_worker_dependencies_healthcheck_disposes_engine_when_redis_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine(FakeConnection())
    monkeypatch.setattr(
        healthcheck_module,
        "create_async_db_engine",
        Mock(return_value=engine),
    )
    monkeypatch.setattr(
        healthcheck_module,
        "_create_redis_client",
        Mock(side_effect=ValueError("invalid Redis configuration")),
    )

    with pytest.raises(ValueError, match="invalid Redis configuration"):
        await healthcheck_module.check_worker_dependencies(*make_settings())

    assert engine.dispose_count == 1


@pytest.mark.asyncio
async def test_worker_dependencies_healthcheck_closes_resources_when_redis_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine(FakeConnection())
    redis_client = FakeRedis(error=OSError("redis unavailable"))
    install_fakes(monkeypatch, engine, redis_client)

    with pytest.raises(OSError, match="redis unavailable"):
        await healthcheck_module.check_worker_dependencies(*make_settings())

    assert redis_client.close_count == 1
    assert engine.dispose_count == 1


@pytest.mark.asyncio
async def test_worker_dependencies_healthcheck_has_a_global_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine(FakeConnection(wait_forever=asyncio.Event()))
    redis_client = FakeRedis()
    install_fakes(monkeypatch, engine, redis_client)

    with pytest.raises(TimeoutError):
        await healthcheck_module.check_worker_dependencies(
            *make_settings(),
            timeout_seconds=0.01,
        )

    assert redis_client.ping_count == 0
    assert redis_client.close_count == 1
    assert engine.dispose_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf")])
async def test_worker_dependencies_healthcheck_rejects_unbounded_timeout(
    timeout_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="fini et strictement positif"):
        await healthcheck_module.check_worker_dependencies(
            *make_settings(),
            timeout_seconds=timeout_seconds,
        )


def test_worker_healthcheck_main_returns_zero_when_dependencies_are_available(
    monkeypatch: pytest.MonkeyPatch,
    preserve_test_logging_configuration: Mock,
) -> None:
    async def successful_healthcheck() -> None:
        return None

    monkeypatch.setattr(
        healthcheck_module,
        "_run_from_environment",
        successful_healthcheck,
    )

    assert healthcheck_module.main() == 0
    preserve_test_logging_configuration.assert_called_once_with(
        service="worker-healthcheck"
    )


def test_worker_healthcheck_main_returns_one_without_logging_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_healthcheck() -> None:
        raise RuntimeError("redis://user:secret@redis:6379/0")

    monkeypatch.setattr(
        healthcheck_module,
        "_run_from_environment",
        failing_healthcheck,
    )

    with caplog.at_level(logging.ERROR):
        exit_code = healthcheck_module.main()

    assert exit_code == 1
    record = caplog.records[-1]
    assert record.event == (  # type: ignore[attr-defined]
        "worker_dependency_healthcheck_failed"
    )
    assert record.service == "worker-healthcheck"  # type: ignore[attr-defined]
    assert record.error_type == "RuntimeError"  # type: ignore[attr-defined]
    assert "secret" not in caplog.text
