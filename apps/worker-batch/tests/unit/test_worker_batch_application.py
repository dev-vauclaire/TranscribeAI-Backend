import pytest

import worker_batch.application as application
from transcribe_ai_shared import DatabaseSettings, JobType, RedisSettings
from worker_batch.config import WorkerBatchSettings


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_run_delegates_to_the_shared_runtime_with_batch_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    transcriber = object()
    on_result = object()

    class StopWorker(Exception):
        pass

    async def fake_run_worker(**arguments: object) -> None:
        captured.update(arguments)
        raise StopWorker

    monkeypatch.setattr(application, "run_worker", fake_run_worker)

    database_settings = DatabaseSettings(
        url="postgresql://postgres:postgres@localhost/postgres"
    )
    redis_settings = RedisSettings(redis_url="redis://localhost:6379/0")
    worker_settings = WorkerBatchSettings(worker_id="batch-1")

    with pytest.raises(StopWorker):
        await application.run(
            database_settings,
            redis_settings,
            worker_settings,
            transcriber,
            on_result,
        )

    assert captured["database_settings"] is database_settings
    assert captured["redis_settings"] is redis_settings
    assert captured["worker_settings"] is worker_settings
    assert captured["transcriber"] is transcriber
    assert captured["job_type"] is JobType.BATCH
    assert captured["on_result"] is on_result
