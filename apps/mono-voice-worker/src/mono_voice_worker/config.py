from transcribe_ai_shared import BaseConfig
import os
# Configuration spécifique aux worker mono voice
class WorkerMonoVoiceConfig(BaseConfig.BaseConfig):
    WHISPER_SERVICE_URL = os.getenv("WHISPER_SERVICE_URL", "http://localhost:5002")