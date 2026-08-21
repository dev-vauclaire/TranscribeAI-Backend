from dispatcher.config import DispatcherSettings
from dispatcher.models import DispatchBatchResult
from dispatcher.postgresql import PostgresDispatchJobStore
from dispatcher.service import DispatcherService
from transcribe_ai_shared import (
    DatabaseSettings,
    RedisSettings,
    RedisTranscriptionStreams,
    create_async_db_engine,
    create_async_session_factory,
)


async def run_dispatch_batch(
    database_settings: DatabaseSettings,
    redis_settings: RedisSettings,
    dispatcher_settings: DispatcherSettings,
) -> DispatchBatchResult:
    """Compose les adaptateurs, traite un batch puis libère leurs ressources."""
    engine = create_async_db_engine(database_settings)
    try:
        streams = RedisTranscriptionStreams(str(redis_settings.redis_url))
        try:
            session_factory = create_async_session_factory(engine)
            store = PostgresDispatchJobStore(session_factory)
            service = DispatcherService(
                job_store=store,
                streams=streams,
            )
            return await service.dispatch_batch(dispatcher_settings.batch_size)
        finally:
            await streams.aclose()
    finally:
        await engine.dispose()
