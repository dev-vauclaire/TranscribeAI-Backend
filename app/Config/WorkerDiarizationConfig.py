from . import BaseConfig
import os
# Configuration spécifique aux workers de diarization
class WorkerDiarizationConfig(BaseConfig.BaseConfig):
    WHISPERX_SERVICE_URL = os.getenv("WHISPERX_SERVICE_URL", "http://localhost:5001")
