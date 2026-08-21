from collections.abc import Callable
from datetime import timedelta
from typing import NoReturn

from transcribe_ai_shared.database.config import DatabaseSettings
from transcribe_ai_shared.database.engine import create_async_db_engine
from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.database.session import create_async_session_factory
from transcribe_ai_shared.queue.config import RedisSettings
from transcribe_ai_shared.queue.redis_streams import RedisTranscriptionStreams
from transcribe_ai_shared.worker.models import WorkerProcessResult
from transcribe_ai_shared.worker.postgresql import PostgresWorkerJobStore
from transcribe_ai_shared.worker.protocols import Transcriber
from transcribe_ai_shared.worker.runtime import WorkerRuntime
from transcribe_ai_shared.worker.worker_settings import WorkerSettings


async def run_worker(
    *,
    database_settings: DatabaseSettings,
    redis_settings: RedisSettings,
    worker_settings: WorkerSettings,
    transcriber: Transcriber,
    job_type: JobType,
    on_result: Callable[[WorkerProcessResult], None] | None = None,
) -> NoReturn:
    """Compose les adaptateurs communs puis consomme les nouveaux messages.

    Le moteur PostgreSQL et le client Redis appartiennent à cette invocation :
    ils sont donc fermés ici, y compris lorsque le claim ou le transcriber
    échoue. Les applications FAST et BATCH ne choisissent que ``job_type`` et
    leur implémentation de ``Transcriber``. La boucle reste ici afin que ces
    ressources et le futur modèle ML soient conservés entre deux messages.
    """
    engine = create_async_db_engine(database_settings)
    try:
        streams = RedisTranscriptionStreams(str(redis_settings.redis_url))
        try:
            session_factory = create_async_session_factory(engine)
            runtime = WorkerRuntime(
                streams=streams,
                job_store=PostgresWorkerJobStore(
                    session_factory,
                    expected_job_type=job_type,
                ),
                transcriber=transcriber,
                job_type=job_type,
                group_name=worker_settings.worker_consumer_group,
                worker_id=worker_settings.worker_id,
                lease_duration=timedelta(
                    seconds=worker_settings.worker_lease_seconds,
                ),
            )
            await runtime.initialize()
            while True:
                result = await runtime.process_next(
                    block_milliseconds=worker_settings.worker_block_milliseconds,
                )
                if on_result is not None:
                    on_result(result)
        finally:
            await streams.aclose()
    finally:
        await engine.dispose()
