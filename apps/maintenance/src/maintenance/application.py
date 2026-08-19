from collections.abc import Collection
from uuid import UUID

from transcribe_ai_shared import (
    DatabaseSettings,
    FileSystemAudioStorage,
    JobRepository,
    StorageSettings,
    TranscriptionJob,
    create_async_db_engine,
    create_async_session_factory,
)

from maintenance.config import MaintenanceSettings
from maintenance.storage_cleanup import StorageCleanupResult, StorageCleanupService


async def run_cleanup_storage(
    database_settings: DatabaseSettings,
    storage_settings: StorageSettings,
    maintenance_settings: MaintenanceSettings,
) -> StorageCleanupResult:
    """Compose les adaptateurs, exécute un cleanup puis libère PostgreSQL."""
    engine = create_async_db_engine(database_settings)
    try:
        session_factory = create_async_session_factory(engine)
        storage = FileSystemAudioStorage(storage_settings.audio_storage_path)

        async def load_jobs(
            job_uuids: Collection[UUID],
        ) -> list[TranscriptionJob]:
            """Ferme la session de lecture avant les suppressions filesystem."""
            async with session_factory() as session:
                repository = JobRepository(session)
                return await repository.get_jobs_by_uuids(job_uuids)

        service = StorageCleanupService(
            storage=storage,
            load_jobs=load_jobs,
            grace_period=maintenance_settings.cleanup_grace_period,
        )
        return await service.cleanup()
    finally:
        await engine.dispose()
