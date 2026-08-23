from decimal import Decimal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024
DEFAULT_FAST_MAX_DURATION_SECONDS = Decimal("900")
DEFAULT_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS = Decimal("14400")


class ApiSettings(BaseSettings):
    """Configuration HTTP et règles du use case de création."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    host: str = Field(default="127.0.0.1", validation_alias="API_HOST")
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="API_PORT")
    max_upload_size_bytes: int = Field(
        default=DEFAULT_MAX_UPLOAD_SIZE_BYTES,
        gt=0,
        validation_alias="API_MAX_UPLOAD_SIZE_BYTES",
    )
    ffprobe_path: str = Field(default="ffprobe", validation_alias="FFPROBE_PATH")
    ffprobe_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        allow_inf_nan=False,
        validation_alias="FFPROBE_TIMEOUT_SECONDS",
    )
    fast_max_duration_seconds: Decimal = Field(
        default=DEFAULT_FAST_MAX_DURATION_SECONDS,
        gt=0,
        allow_inf_nan=False,
        validation_alias="API_FAST_MAX_DURATION_SECONDS",
    )
    long_form_diarization_max_duration_seconds: Decimal = Field(
        default=DEFAULT_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS,
        gt=0,
        allow_inf_nan=False,
        validation_alias="API_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS",
    )

    @field_validator("host", "ffprobe_path")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("la valeur ne peut pas être vide")
        return normalized

    @model_validator(mode="after")
    def validate_duration_limits(self) -> "ApiSettings":
        """Ordonne les limites des profils FAST et avec diarisation."""
        if (
            self.fast_max_duration_seconds
            > self.long_form_diarization_max_duration_seconds
        ):
            raise ValueError(
                "API_FAST_MAX_DURATION_SECONDS doit être inférieur ou égal "
                "à API_LONG_FORM_DIARIZATION_MAX_DURATION_SECONDS"
            )
        return self
