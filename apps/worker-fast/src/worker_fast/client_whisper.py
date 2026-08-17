import mimetypes
from typing import BinaryIO

import requests
from pydantic import BaseModel, ValidationError
from requests.exceptions import RequestException
from requests_toolbelt.multipart.encoder import MultipartEncoder


class FilteredSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str


class WhisperPayload(BaseModel):
    full_text: str
    segments: list[FilteredSegment]
    language: str | None = None


class WhisperClientError(Exception):
    """Erreur produite lors d'un échange avec le service Whisper."""

    pass


class ClientWhisper:
    def __init__(
        self,
        base_url: str,
        timeout: float = 600,
        healthcheck_timeout: float = 10,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.healthcheck_timeout = healthcheck_timeout

    def check_whisper_connection(self) -> None:
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=self.healthcheck_timeout,
            )
            response.raise_for_status()
        except RequestException as error:
            raise WhisperClientError(
                "Impossible de contacter le service Whisper"
            ) from error

    def send_to_whisper_service(
        self,
        audio_file: BinaryIO,
        *,
        filename: str,
    ) -> WhisperPayload:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        multipart = MultipartEncoder(
            fields={
                "audioFile": (filename, audio_file, content_type),
            }
        )
        try:
            response = requests.post(
                f"{self.base_url}/BatchTranscriptionService",
                data=multipart,
                headers={"Content-Type": multipart.content_type},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return WhisperPayload.model_validate(response.json())
        except (RequestException, ValidationError) as error:
            raise WhisperClientError("La transcription Whisper a échoué") from error

    def cancel_transcription(self, job_id: str) -> bool:
        try:
            response = requests.post(
                f"{self.base_url}/cancel",
                json={"job_id": job_id},
                timeout=self.timeout,
            )
            return response.status_code == 200
        except RequestException as error:
            raise WhisperClientError(
                "L'annulation de la transcription Whisper a échoué"
            ) from error
