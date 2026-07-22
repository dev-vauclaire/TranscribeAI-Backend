import mimetypes
from typing import BinaryIO

import requests
from pydantic import BaseModel
from requests_toolbelt.multipart.encoder import MultipartEncoder

# Modèles de données pour la communication avec le service Whisper
class FilteredSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str

class WhisperPayload(BaseModel):
    full_text: str
    segments: list[FilteredSegment]
    language: str | None = None

# Client pour la communication http avec le service de transcription
class ClientWhisper:
    def __init__(self, BASE_URL : str, timeout : int = 600):
        self.BASE_URL = BASE_URL
        self.timeout = timeout

    def test_connection(self) -> bool:
        response = requests.get(f"{self.BASE_URL}/health", timeout=self.timeout)
        response.raise_for_status()

    # Fonction pour envoyer le fichier audio au service Whisper et obtenir la transcription
    def send_to_whisper_service(
        self,
        audio_file: BinaryIO,
        *,
        filename: str,
    ) -> WhisperPayload:
        content_type = (
            mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )
        multipart = MultipartEncoder(
            fields={
                "audioFile": (filename, audio_file, content_type),
            }
        )
        response = requests.post(
            f"{self.BASE_URL}/BatchTranscriptionService",
            data=multipart,
            headers={"Content-Type": multipart.content_type},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return WhisperPayload.model_validate(response.json())
    
    # Fonction pour annuler une transcription en cours (si supporté par le service Whisper
    def cancel_transcription(self, job_id: str) -> bool:
        response = requests.post(f"{self.BASE_URL}/cancel", json={"job_id": job_id})
        return response.status_code == 200
