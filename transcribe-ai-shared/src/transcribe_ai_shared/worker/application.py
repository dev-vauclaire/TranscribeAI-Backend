from collections.abc import Callable
from datetime import timedelta
from time import monotonic as monotonic_clock
from typing import NoReturn

from transcribe_ai_shared.database.config import DatabaseSettings
from transcribe_ai_shared.database.engine import create_async_db_engine
from transcribe_ai_shared.database.models import JobType
from transcribe_ai_shared.database.session import create_async_session_factory
from transcribe_ai_shared.queue.config import RedisSettings
from transcribe_ai_shared.queue.redis_streams import RedisTranscriptionStreams
from transcribe_ai_shared.worker.completion import TranscriptionCompletionService
from transcribe_ai_shared.worker.failure import TranscriptionFailureService
from transcribe_ai_shared.worker.models import WorkerIdle, WorkerProcessResult
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
    monotonic: Callable[[], float] = monotonic_clock,
) -> NoReturn:
    """Compose les adaptateurs puis alterne recovery pending et nouveaux messages.

    Le moteur PostgreSQL et le client Redis appartiennent à cette invocation :
    ils sont donc fermés ici, y compris lorsque le claim ou le transcriber
    échoue. Les applications FAST et LONG_FORM_DIARIZATION ne choisissent que
    ``job_type`` et leur implémentation de ``Transcriber``. La boucle reste ici
    afin que ces ressources et le futur modèle ML soient conservés entre deux
    messages.
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
                completer=TranscriptionCompletionService(session_factory),
                failure_handler=TranscriptionFailureService(
                    session_factory,
                    max_attempts=worker_settings.max_attempts,
                ),
                job_type=job_type,
                group_name=worker_settings.worker_consumer_group,
                worker_id=worker_settings.worker_id,
                lease_duration=timedelta(
                    seconds=worker_settings.worker_lease_seconds,
                ),
                heartbeat_interval=timedelta(
                    seconds=worker_settings.worker_heartbeat_seconds,
                ),
            )
            await runtime.initialize()
            next_autoclaim_at = monotonic()
            while True:
                if monotonic() >= next_autoclaim_at:
                    pending_result = await runtime.process_next_pending(
                        min_idle_milliseconds=(
                            worker_settings.worker_autoclaim_min_idle_milliseconds
                        ),
                    )
                    next_autoclaim_at = (
                        monotonic() + worker_settings.worker_autoclaim_interval_seconds
                    )
                    if not isinstance(pending_result, WorkerIdle):
                        if on_result is not None:
                            on_result(pending_result)
                        continue

                result = await runtime.process_next(
                    block_milliseconds=worker_settings.worker_block_milliseconds,
                )
                if on_result is not None:
                    on_result(result)
        finally:
            await streams.aclose()
    finally:
        await engine.dispose()
