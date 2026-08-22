from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from transcribe_ai_shared.queue.models import MAX_ATTEMPT_COUNT


WorkerId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
ConsumerGroupName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class WorkerSettings(BaseSettings):
    """Configuration commune à tous les processus worker."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    worker_id: WorkerId
    worker_consumer_group: ConsumerGroupName = "transcription-workers"
    # RedisTranscriptionStreams utilise actuellement un timeout socket de 10 s.
    worker_block_milliseconds: int = Field(default=5_000, gt=0, lt=10_000)
    worker_lease_seconds: int = Field(default=300, gt=0)
    worker_heartbeat_seconds: int = Field(default=60, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=MAX_ATTEMPT_COUNT + 1)

    @model_validator(mode="after")
    def validate_heartbeat_interval(self) -> Self:
        """Garantit au heartbeat une marge avant l'expiration du lease."""
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError(
                "WORKER_HEARTBEAT_SECONDS doit être strictement inférieur "
                "à WORKER_LEASE_SECONDS"
            )
        return self
