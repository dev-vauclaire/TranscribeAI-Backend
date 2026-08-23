from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from api.Controllers import create_transcription, get_transcription
from api.Routes.dependencies import (
    get_transcription_creation_service,
    get_transcription_query_service,
    get_upload_metadata_validator,
)
from api.Schemas import (
    TranscriptionCreatedResponse,
    TranscriptionCreationRequest,
    TranscriptionStatusResponse,
)
from api.Services.create_transcription import CreateTranscriptionService
from api.Services.get_transcription import GetTranscriptionService
from api.Validators.upload_metadata import UploadMetadataValidator
from transcribe_ai_shared import JobType


router = APIRouter(tags=["transcriptions"])


@router.post(
    "/transcriptions",
    response_model=TranscriptionCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "headers": {
                "Location": {
                    "description": "URL de consultation du job créé.",
                    "schema": {"type": "string"},
                }
            }
        },
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "Le fichier dépasse la taille maximale configurée."
        },
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {
            "description": (
                "L'extension, le MIME, le conteneur ou le codec n'est pas supporté."
            )
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Le formulaire ou le contenu audio est invalide."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": (
                "Le stockage, PostgreSQL ou la validation audio est indisponible."
            )
        },
    },
)
async def post_transcription(
    response: Response,
    audio_file: Annotated[
        UploadFile,
        File(description="Fichier WAV, MP3, OGG ou M4A à transcrire."),
    ],
    transcription_type: Annotated[
        JobType,
        Form(
            alias="type",
            description="File de traitement FAST ou LONG_FORM_DIARIZATION.",
        ),
    ],
    service: Annotated[
        CreateTranscriptionService,
        Depends(get_transcription_creation_service),
    ],
    upload_metadata_validator: Annotated[
        UploadMetadataValidator,
        Depends(get_upload_metadata_validator),
    ],
) -> TranscriptionCreatedResponse:
    """Associe le contrat multipart au contrôleur de transcription."""
    request_data = TranscriptionCreationRequest(job_type=transcription_type)
    return await create_transcription(
        audio_file=audio_file,
        request_data=request_data,
        response=response,
        service=service,
        upload_metadata_validator=upload_metadata_validator,
    )


@router.get(
    "/transcriptions/{job_uuid}",
    response_model=TranscriptionStatusResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Aucune transcription ne correspond à cet UUID."
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "L'identifiant fourni n'est pas un UUID valide."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": (
                "PostgreSQL est indisponible ou le résultat durable attendu est absent."
            )
        },
    },
)
async def get_transcription_status(
    job_uuid: UUID,
    response: Response,
    service: Annotated[
        GetTranscriptionService,
        Depends(get_transcription_query_service),
    ],
) -> TranscriptionStatusResponse:
    """Associe l'UUID public au contrôleur de consultation."""
    return await get_transcription(
        job_uuid=job_uuid,
        response=response,
        service=service,
    )
