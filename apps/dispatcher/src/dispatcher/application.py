from dispatcher.config import DispatcherSettings
from dispatcher.models import DispatcherCycleResult
from dispatcher.postgresql import PostgresDispatchJobStore
from dispatcher.recovery import LeaseRecoveryService
from dispatcher.service import DispatcherService
from transcribe_ai_shared import (
    DatabaseSettings,
    RedisSettings,
    RedisTranscriptionStreams,
    create_async_db_engine,
    create_async_session_factory,
)


async def run_dispatch_cycle(
    database_settings: DatabaseSettings,
    redis_settings: RedisSettings,
    dispatcher_settings: DispatcherSettings,
) -> DispatcherCycleResult:
    """Récupère les leases expirés, dispatche un batch puis ferme les ressources."""
    engine = create_async_db_engine(database_settings)
    try:
        streams = RedisTranscriptionStreams(str(redis_settings.redis_url))
        try:
            session_factory = create_async_session_factory(engine)
            store = PostgresDispatchJobStore(session_factory)
            recovery_service = LeaseRecoveryService(job_store=store)
            dispatch_service = DispatcherService(
                job_store=store,
                streams=streams,
            )
            recovery_result = await recovery_service.recover_batch(
                dispatcher_settings.batch_size,
                dispatcher_settings.max_attempts,
            )
            dispatch_result = await dispatch_service.dispatch_batch(
                dispatcher_settings.batch_size
            )
            return DispatcherCycleResult(
                recovery=recovery_result,
                dispatch=dispatch_result,
            )
        finally:
            await streams.aclose()
    finally:
        await engine.dispose()
