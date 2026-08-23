from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from transcribe_ai_shared.retry_policy import MAX_TOTAL_ATTEMPTS


class DispatcherSettings(BaseSettings):
    """Configuration de l'exécution one-shot du dispatcher."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        env_prefix="DISPATCHER_",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    batch_size: int = Field(default=100, gt=0, le=1_000)
    reconciliation_timeout_seconds: int = Field(
        default=300,
        gt=0,
        validation_alias="RECONCILIATION_TIMEOUT_SECONDS",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=MAX_TOTAL_ATTEMPTS,
        validation_alias="MAX_ATTEMPTS",
    )
