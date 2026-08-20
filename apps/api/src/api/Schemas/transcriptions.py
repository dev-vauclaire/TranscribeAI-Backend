from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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
