from fastapi import Request

from api.Services.create_transcription import CreateTranscriptionService
from api.Validators.upload_metadata import UploadMetadataValidator


async def get_transcription_creation_service(
    request: Request,
) -> CreateTranscriptionService:
    """Résout le service construit par la factory sans dépendance globale."""
    service: CreateTranscriptionService = (
        request.app.state.transcription_creation_service
    )
    return service


async def get_upload_metadata_validator(
    request: Request,
) -> UploadMetadataValidator:
    """Résout le filtre HTTP construit par la factory d'application."""
    validator: UploadMetadataValidator = request.app.state.upload_metadata_validator
    return validator
