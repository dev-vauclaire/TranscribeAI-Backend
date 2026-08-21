from collections.abc import Callable
from typing import NoReturn

from worker_batch.config import WorkerBatchSettings
from transcribe_ai_shared import (
    DatabaseSettings,
    JobType,
    RedisSettings,
    Transcriber,
    WorkerProcessResult,
    run_worker,
)


async def run(
    database_settings: DatabaseSettings,
    redis_settings: RedisSettings,
    worker_settings: WorkerBatchSettings,
    transcriber: Transcriber,
    on_result: Callable[[WorkerProcessResult], None] | None = None,
) -> NoReturn:
    """Consomme durablement le stream BATCH avec le transcriber injecté."""
    await run_worker(
        database_settings=database_settings,
        redis_settings=redis_settings,
        worker_settings=worker_settings,
        transcriber=transcriber,
        job_type=JobType.BATCH,
        on_result=on_result,
    )
