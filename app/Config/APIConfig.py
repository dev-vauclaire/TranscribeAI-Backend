from app.Config import BaseConfig
import os 
# Configuration spécifique à l'API Flask
class APIConfig(BaseConfig.BaseConfig):
    API_PORT = int(os.getenv("API_PORT", "5000"))
    HOST = "0.0.0.0"

    # Lié à l'upload des fichiers audio
    raw_formats = os.getenv("FORMAT_AUDIO_ALLOWED", "wav,mp3,ogg,m4a")
    FORMAT_AUDIO_ALLOWED = {f.strip() for f in raw_formats.split(",")}
    MAX_AUDIO_SIZE_MB = int(os.getenv("MAX_AUDIO_SIZE_MB", "100"))

