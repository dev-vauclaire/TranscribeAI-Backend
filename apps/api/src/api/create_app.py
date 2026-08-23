from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from api.Media.ffprobe import FFprobeMediaProbe
from api.Media.protocols import MediaProbe
from api.Routes import api_router
from api.Services.create_transcription import CreateTranscriptionService
from api.Services.get_transcription import GetTranscriptionService
from api.Services.protocols import (
    JobReadRepositoryFactory,
    JobRepositoryFactory,
    ResultRepositoryFactory,
)
from api.Validators.upload_metadata import UploadMetadataValidator
from api.config import ApiSettings
from transcribe_ai_shared import (
    AsyncSessionFactory,
    AudioStorage,
    DatabaseSettings,
    FileSystemAudioStorage,
    JobRepository,
    ResultRepository,
    StorageSettings,
    create_async_db_engine,
    create_async_session_factory,
)


def create_app(
    *,
    settings: ApiSettings | None = None,
    transcription_service: CreateTranscriptionService | None = None,
    transcription_query_service: GetTranscriptionService | None = None,
    storage: AudioStorage | None = None,
    media_probe: MediaProbe | None = None,
    upload_metadata_validator: UploadMetadataValidator | None = None,
    session_factory: AsyncSessionFactory | None = None,
    repository_factory: JobRepositoryFactory = JobRepository,
    job_read_repository_factory: JobReadRepositoryFactory = JobRepository,
    result_repository_factory: ResultRepositoryFactory = ResultRepository,
) -> FastAPI:
    """Construit l'application et permet l'injection de ses frontières en test."""
    active_settings = settings or ApiSettings()
    owned_engine: AsyncEngine | None = None
    active_session_factory = session_factory

    def resolve_session_factory() -> AsyncSessionFactory:
        """Construit au plus une fois la frontière PostgreSQL partagée."""
        nonlocal active_session_factory, owned_engine
        if active_session_factory is None:
            owned_engine = create_async_db_engine(DatabaseSettings())
            active_session_factory = create_async_session_factory(owned_engine)
        return active_session_factory

    if transcription_service is not None:
        if any(dependency is not None for dependency in (storage, media_probe)):
            raise ValueError(
                "transcription_service ne peut pas être combiné avec ses dépendances."
            )
        active_service = transcription_service
    else:
        active_storage = storage or FileSystemAudioStorage(
            StorageSettings().audio_storage_path
        )
        active_probe = media_probe or FFprobeMediaProbe(
            ffprobe_path=active_settings.ffprobe_path,
            timeout_seconds=active_settings.ffprobe_timeout_seconds,
        )
        active_service = CreateTranscriptionService(
            storage=active_storage,
            media_probe=active_probe,
            session_factory=resolve_session_factory(),
            fast_max_duration_seconds=active_settings.fast_max_duration_seconds,
            long_form_diarization_max_duration_seconds=(
                active_settings.long_form_diarization_max_duration_seconds
            ),
            repository_factory=repository_factory,
        )

    if transcription_query_service is not None:
        active_query_service = transcription_query_service
    else:
        active_query_service = GetTranscriptionService(
            session_factory=resolve_session_factory(),
            job_repository_factory=job_read_repository_factory,
            result_repository_factory=result_repository_factory,
        )

    active_upload_validator = upload_metadata_validator or UploadMetadataValidator(
        active_settings.max_upload_size_bytes
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            if owned_engine is not None:
                await owned_engine.dispose()

    app = FastAPI(
        title="Transcribe AI API",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
    )
    app.state.transcription_creation_service = active_service
    app.state.transcription_query_service = active_query_service
    app.state.upload_metadata_validator = active_upload_validator
    app.include_router(api_router)
    return app
