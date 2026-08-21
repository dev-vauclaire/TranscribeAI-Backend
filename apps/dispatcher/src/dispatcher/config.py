from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DispatcherSettings(BaseSettings):
    """Configuration de l'exécution one-shot du dispatcher."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        env_prefix="DISPATCHER_",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    batch_size: int = Field(default=100, gt=0, le=1_000)
