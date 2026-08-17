from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from transcribe_ai_shared.database.schemas.types import (
    NonNegativeInt,
    ShortNonEmptyString,
)


class OutboxEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    event_uuid: UUID
    job_uuid: UUID
    event_type: ShortNonEmptyString
    created_at: AwareDatetime
    published_at: AwareDatetime | None = None
    locked_at: AwareDatetime | None = None
    locked_by: ShortNonEmptyString | None = None
    attempt_count: NonNegativeInt
    last_error: str | None = None

    @model_validator(mode="after")
    def validate_lock_fields(self) -> "OutboxEventSchema":
        if (self.locked_at is None) != (self.locked_by is None):
            raise ValueError("locked_at et locked_by doivent être renseignés ensemble")
        return self
