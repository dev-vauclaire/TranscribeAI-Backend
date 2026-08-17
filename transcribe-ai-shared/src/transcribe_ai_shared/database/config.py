from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Configuration de la connexion et du pool PostgreSQL."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        env_prefix="DATABASE_",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    url: PostgresDsn = Field(repr=False)
    echo: bool = False
    pool_size: int = Field(default=4, gt=0)
    max_overflow: int = Field(default=0, ge=0)
    pool_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("url")
    @classmethod
    def require_sync_psycopg2_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme not in {"postgresql", "postgresql+psycopg2"}:
            raise ValueError(
                "url doit utiliser le pilote PostgreSQL synchrone psycopg2"
            )
        return value
