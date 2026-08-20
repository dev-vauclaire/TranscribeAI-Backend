from uuid import UUID

from fastapi import HTTPException, Response, UploadFile, status

from api.Schemas import (
    TranscriptionCreatedResponse,
    TranscriptionCreationRequest,
    TranscriptionStatusResponse,
)
from api.Services.create_transcription import CreateTranscriptionService
from api.Services.get_transcription import GetTranscriptionService
from api.Validators.upload_metadata import UploadMetadataValidator
from api.exceptions import (
    AudioStorageUnavailableError,
    AudioTooLongError,
    EmptyUploadError,
    InvalidAudioFileError,
    MediaProbeUnavailableError,
    MissingUploadFilenameError,
    TranscriptionPersistenceError,
    TranscriptionNotFoundError,
    TranscriptionQueryError,
    UnsupportedAudioCodecError,
    UnsupportedAudioFormatError,
    UnsupportedDeclaredMediaTypeError,
    UnsupportedFileExtensionError,
    UploadSizeUnavailableError,
    UploadTooLargeError,
)


async def create_transcription(
    *,
    audio_file: UploadFile,
    request_data: TranscriptionCreationRequest,
    response: Response,
    service: CreateTranscriptionService,
    upload_metadata_validator: UploadMetadataValidator,
) -> TranscriptionCreatedResponse:
    """Traduit l'upload HTTP en appel applicatif puis construit la réponse 202."""
    try:
        upload_metadata = upload_metadata_validator.validate(audio_file)
        result = await service.create(
            source=audio_file.file,
            extension=upload_metadata.extension,
            job_type=request_data.job_type,
        )
    except UploadTooLargeError as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(error),
        ) from error
    except (
        UnsupportedFileExtensionError,
        UnsupportedDeclaredMediaTypeError,
        UnsupportedAudioFormatError,
        UnsupportedAudioCodecError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(error),
        ) from error
    except (
        MissingUploadFilenameError,
        EmptyUploadError,
        UploadSizeUnavailableError,
        InvalidAudioFileError,
        AudioTooLongError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (
        AudioStorageUnavailableError,
        MediaProbeUnavailableError,
        TranscriptionPersistenceError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le service de transcription est temporairement indisponible.",
        ) from error

    response.headers["Location"] = f"/api/transcriptions/{result.job_uuid}"
    return TranscriptionCreatedResponse(
        job_uuid=result.job_uuid,
        status=result.status,
    )


async def get_transcription(
    *,
    job_uuid: UUID,
    response: Response,
    service: GetTranscriptionService,
) -> TranscriptionStatusResponse:
    """Traduit le use case de consultation en réponse HTTP stable."""
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await service.get(job_uuid)
    except TranscriptionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La transcription demandée n'existe pas.",
        ) from error
    except TranscriptionQueryError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Le statut de la transcription est temporairement indisponible.",
        ) from error

    return TranscriptionStatusResponse(
        job_uuid=result.job_uuid,
        status=result.status,
        result=result.result,
    )
