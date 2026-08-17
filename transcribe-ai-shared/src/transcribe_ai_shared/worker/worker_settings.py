from typing import Annotated

from pydantic import Field, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


WorkerId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
    worker_lease_seconds: int = Field(default=300, gt=0)
    max_attempts: int = Field(default=3, ge=1)
