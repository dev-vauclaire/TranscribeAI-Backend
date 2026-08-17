from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseSettings):
    """Configuration du stockage local des fichiers audio."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    audio_storage_path: Path = Field(
        default=Path("tmp/audios_buffers"),
        repr=False,
    )

    @field_validator("audio_storage_path", mode="before")
    @classmethod
    def reject_empty_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("audio_storage_path ne peut pas être vide")
        return value
