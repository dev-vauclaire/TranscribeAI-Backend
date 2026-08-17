from pydantic import Field

from transcribe_ai_shared import WorkerSettings


class WorkerMonoVoiceSettings(WorkerSettings):
    whisper_service_url: str = Field(default="http://localhost:5002", repr=False)
    redis_queue_name_mono_voice: str = Field(
        default="mono_voice_queue",
        repr=False,
    )
    worker_loop_sleep_time: int = Field(default=1, gt=0)
