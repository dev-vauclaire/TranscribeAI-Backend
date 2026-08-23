from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from transcribe_ai_shared import WorkerSettings


TranscriberModel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
FasterWhisperComputeType = Literal[
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
]


class WorkerFastSettings(WorkerSettings):
    """Configuration du worker FAST et du backend injecté au runtime."""

    worker_environment: Literal["development", "production"] = "production"
    worker_transcriber_backend: Literal["faster-whisper", "fake"] = "faster-whisper"
    worker_transcriber_model: TranscriberModel = "large-v3-turbo"
    worker_transcriber_device: Literal["cuda", "cpu"] = "cuda"
    worker_transcriber_compute_type: FasterWhisperComputeType = "float16"

    @model_validator(mode="after")
    def allow_fake_transcriber_only_in_development(self) -> Self:
        """Empêche l'utilisation accidentelle du fake dans un environnement réel."""
        if (
            self.worker_transcriber_backend == "fake"
            and self.worker_environment != "development"
        ):
            raise ValueError(
                "Le backend fake est réservé à WORKER_ENVIRONMENT=development"
            )
        return self
