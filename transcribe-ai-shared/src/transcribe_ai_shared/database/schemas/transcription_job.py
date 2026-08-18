from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from transcribe_ai_shared.database.models import JobStatus, JobType
from transcribe_ai_shared.database.schemas.types import (
    NonEmptyString,
    NonNegativeInt,
    ShortNonEmptyString,
)


class TranscriptionJobSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    job_uuid: UUID
    status: JobStatus
    job_type: JobType
    audio_uri: NonEmptyString
    dispatch_required: bool
    last_dispatched_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    attempt_count: NonNegativeInt
    last_error: str | None = None
    lease_owner: ShortNonEmptyString | None = None
    lease_expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_lease_fields(self) -> "TranscriptionJobSchema":
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError(
                "lease_owner et lease_expires_at doivent être renseignés ensemble"
            )
        return self
