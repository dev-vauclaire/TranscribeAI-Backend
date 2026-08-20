from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from transcribe_ai_shared import JobStatus, JobType


class TranscriptionCreationRequest(BaseModel):
    """Champs métier du formulaire de création d'une transcription."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_type: JobType


class TranscriptionCreatedResponse(BaseModel):
    """Réponse retournée lorsqu'un job est durablement mis en attente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_uuid: UUID
    status: Literal[JobStatus.QUEUED] = JobStatus.QUEUED


class TranscriptionStatusResponse(BaseModel):
    """Statut public du job et résultat JSON lorsqu'il est terminé."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    job_uuid: UUID
    status: JobStatus
    result: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_result_matches_status(self) -> "TranscriptionStatusResponse":
        """Empêche une réponse COMPLETED sans résultat ou un résultat prématuré."""
        if self.status is JobStatus.COMPLETED and self.result is None:
            raise ValueError("un job COMPLETED doit exposer son résultat")
        if self.status is not JobStatus.COMPLETED and self.result is not None:
            raise ValueError("seul un job COMPLETED peut exposer un résultat")
        return self
