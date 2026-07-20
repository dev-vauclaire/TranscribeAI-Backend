from common_packages import BaseConfig
import os
# Configuration spécifique aux workers de diarization
class Config(BaseConfig.BaseConfig):
    WHISPERX_SERVICE_URL = os.getenv("WHISPERX_SERVICE_URL")
