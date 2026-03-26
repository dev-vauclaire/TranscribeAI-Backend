from . import BaseConfig
import os
# Configuration spécifique aux workers batch
class WorkerBatchConfig(BaseConfig.BaseConfig):
    WHISPER_SERVICE_URL = os.getenv("WHISPER_SERVICE_URL", "http://localhost:5002")