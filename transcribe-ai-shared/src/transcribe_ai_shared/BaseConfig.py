from pydantic import (
  Field,
  PostgresDsn,
  RedisDsn,
)

from pydantic_settings import BaseSettings

class TranscribeAiBaseSettings(BaseSettings):
  redis_dsn: RedisDsn = Field(default='redis://localhost:6379/0', repr=False)
  pg_dsn: PostgresDsn = Field(default='postgresql+psycopg2://postgres:postgres@localhost:5432/postgres', repr=False)
  audio_folder_path: str = Field(default='tmp/audios_buffers', repr=False)