import requests

# Service pour gérer les interactions avec le service Whisper
class TranscriptionService:
    def __init__(self, BASE_URL : str):
        self.BASE_URL = BASE_URL

    # Fonction pour envoyer le fichier audio au service Whisper et obtenir la transcription
    def send_to_whisper_service(self, audio_file) -> str:
        files = {
            "audioFile": audio_file
        }
        response = requests.post(f"{self.BASE_URL}/BatchTranscriptionService", files=files)
        response.raise_for_status()
        return response.json()
    
    # Fonction pour annuler une transcription en cours (si supporté par le service Whisper
    def cancel_transcription(self, job_id: str) -> bool:
        response = requests.post(f"{self.BASE_URL}/cancel", json={"job_id": job_id})
        return response.status_code == 200