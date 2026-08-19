from datetime import timedelta

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MaintenanceSettings(BaseSettings):
    """Configuration des tâches one-shot de maintenance."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        env_prefix="MAINTENANCE_",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    cleanup_grace_period_seconds: int = Field(default=3600, gt=0)

    @property
    def cleanup_grace_period(self) -> timedelta:
        """Expose la durée validée sous la forme attendue par le service."""
        return timedelta(seconds=self.cleanup_grace_period_seconds)
