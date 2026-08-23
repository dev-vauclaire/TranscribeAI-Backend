from collections.abc import Callable
from typing import NoReturn

from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings
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
    worker_settings: WorkerLongFormDiarizationSettings,
    transcriber: Transcriber,
    on_result: Callable[[WorkerProcessResult], None] | None = None,
) -> NoReturn:
    """Consomme le stream des transcriptions longues avec diarisation."""
    await run_worker(
        database_settings=database_settings,
        redis_settings=redis_settings,
        worker_settings=worker_settings,
        transcriber=transcriber,
        job_type=JobType.LONG_FORM_DIARIZATION,
        on_result=on_result,
    )
