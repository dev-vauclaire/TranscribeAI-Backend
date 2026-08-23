from typing import Literal, Self

from pydantic import model_validator

from transcribe_ai_shared import WorkerSettings


class WorkerLongFormDiarizationSettings(WorkerSettings):
    """Configuration du worker de transcription longue avec diarisation."""

    worker_environment: Literal["development", "production"] = "production"
    worker_transcriber_backend: Literal["fake"] | None = None

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
