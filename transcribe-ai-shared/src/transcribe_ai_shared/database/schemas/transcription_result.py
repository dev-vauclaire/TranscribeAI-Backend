from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue

from transcribe_ai_shared.database.schemas.types import (
    NonNegativeInt,
    ShortNonEmptyString,
)


class TranscriptionResultSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    job_uuid: UUID
    result: dict[str, JsonValue]
    speaker_count: NonNegativeInt | None = None
    model_name: ShortNonEmptyString | None = None
    model_version: ShortNonEmptyString | None = None
    note: str | None = None
    created_at: AwareDatetime
