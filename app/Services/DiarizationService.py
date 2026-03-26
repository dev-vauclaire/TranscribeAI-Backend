import requests

# Service pour gérer les interactions avec le service Whisperx pour la diarization
class DiarizationService:
    def __init__(self, BASE_URL : str):
        self.BASE_URL = BASE_URL

    # Fonction pour envoyer le fichier audio au service Whisper et obtenir la transcription
    def send_to_whisperx_service(self, audio_file, params) -> str:
        files = {
            "audioFile": audio_file
        }
        data = {
            "min_speakers": params.get("min_speakers"),
            "max_speakers": params.get("max_speakers")
        }
        response = requests.post(f"{self.BASE_URL}/diarize", files=files, data=data)
        response.raise_for_status()
        return response.json()