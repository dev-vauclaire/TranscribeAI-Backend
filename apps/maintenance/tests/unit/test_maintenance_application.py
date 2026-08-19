from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import maintenance.application as application
from maintenance.config import MaintenanceSettings
from transcribe_ai_shared import (
    DatabaseSettings,
    StorageSettings,
)

from maintenance.storage_cleanup import StorageCleanupResult


pytestmark = pytest.mark.unit


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeSessionContext:
    def __init__(self) -> None:
        self.session = object()
        self.closed = False

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_run_cleanup_storage_composes_dependencies_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = FakeEngine()
    session_context = FakeSessionContext()
    job_uuid = uuid4()
    loaded_job = SimpleNamespace(job_uuid=job_uuid)
    expected_result = StorageCleanupResult(1, 1, 0, 0, 1, 0)
    captured: dict[str, object] = {}

    class FakeRepository:
        def __init__(self, session: object) -> None:
            assert session is session_context.session

        async def get_jobs_by_uuids(
            self,
            job_uuids: Collection[UUID],
        ) -> list[object]:
            captured["job_uuids"] = tuple(job_uuids)
            return [loaded_job]

    class FakeService:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def cleanup(self) -> StorageCleanupResult:
            load_jobs = captured["load_jobs"]
            jobs = await load_jobs([job_uuid])  # type: ignore[operator]
            assert jobs == [loaded_job]
            assert session_context.closed is True
            return expected_result

    monkeypatch.setattr(application, "create_async_db_engine", lambda settings: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda current_engine: lambda: session_context,
    )
    monkeypatch.setattr(application, "JobRepository", FakeRepository)
    monkeypatch.setattr(application, "StorageCleanupService", FakeService)

    result = await application.run_cleanup_storage(
        DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
        StorageSettings(audio_storage_path=tmp_path),
        MaintenanceSettings(),
    )

    assert result is expected_result
    assert captured["grace_period"] == MaintenanceSettings().cleanup_grace_period
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_run_cleanup_storage_disposes_engine_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = FakeEngine()

    class FailingService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def cleanup(self) -> StorageCleanupResult:
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(application, "create_async_db_engine", lambda settings: engine)
    monkeypatch.setattr(
        application,
        "create_async_session_factory",
        lambda current_engine: lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(application, "StorageCleanupService", FailingService)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await application.run_cleanup_storage(
            DatabaseSettings(url="postgresql://postgres:postgres@localhost/postgres"),
            StorageSettings(audio_storage_path=tmp_path),
            MaintenanceSettings(),
        )

    assert engine.disposed is True
