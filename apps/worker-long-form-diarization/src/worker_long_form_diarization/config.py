from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator

from transcribe_ai_shared import WorkerSettings


class WorkerLongFormDiarizationSettings(WorkerSettings):
    """Configuration du worker de transcription longue avec diarisation."""

    worker_environment: Literal["development", "production"] = "production"
    worker_transcriber_backend: Literal["whisperx", "fake"] = "whisperx"
    worker_transcriber_model: Literal["large-v3", "large-v3-turbo"] = "large-v3-turbo"
    worker_transcriber_device: Literal["cuda", "cpu"] = "cuda"
    worker_transcriber_compute_type: Literal[
        "default",
        "auto",
        "int8",
        "int8_float32",
        "int8_float16",
        "int8_bfloat16",
        "int16",
        "float16",
        "float32",
        "bfloat16",
    ] = "default"
    worker_transcriber_batch_size: int = Field(default=16, ge=1, le=256)
    worker_transcriber_hugging_face_token: SecretStr | None = Field(
        default=None,
        repr=False,
    )
    worker_transcriber_model_directory: Path = Field(
        default=Path("/models"),
        repr=False,
    )
    worker_transcriber_diarization_model: Literal[
        "pyannote/speaker-diarization-community-1"
    ] = "pyannote/speaker-diarization-community-1"

    @model_validator(mode="after")
    def validate_transcriber_configuration(self) -> Self:
        """Valide les prérequis propres au backend sélectionné."""
        if (
            self.worker_transcriber_backend == "fake"
            and self.worker_environment != "development"
        ):
            raise ValueError(
                "Le backend fake est réservé à WORKER_ENVIRONMENT=development"
            )

        if self.worker_transcriber_backend == "whisperx":
            token = self.worker_transcriber_hugging_face_token
            if token is None or not token.get_secret_value().strip():
                raise ValueError(
                    "WORKER_TRANSCRIBER_HUGGING_FACE_TOKEN est requis avec WhisperX"
                )

        if not self.worker_transcriber_model_directory.is_absolute():
            raise ValueError(
                "WORKER_TRANSCRIBER_MODEL_DIRECTORY doit être un chemin absolu"
            )

        return self
