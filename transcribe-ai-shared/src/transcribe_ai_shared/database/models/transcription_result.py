from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from transcribe_ai_shared.database.base import Base

if TYPE_CHECKING:
    from transcribe_ai_shared.database.models.transcription_job import (
        TranscriptionJob,
    )


class TranscriptionResult(Base):
    __tablename__ = "transcription_results"
    __table_args__ = (
        CheckConstraint(
            "speaker_count IS NULL OR speaker_count >= 0",
            name="speaker_count_non_negative",
        ),
        CheckConstraint(
            "model_name IS NULL OR length(btrim(model_name)) > 0",
            name="model_name_non_empty",
        ),
        CheckConstraint(
            "model_version IS NULL OR length(btrim(model_version)) > 0",
            name="model_version_non_empty",
        ),
    )

    job_uuid: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "transcription_jobs.job_uuid",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        primary_key=True,
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
    )
    speaker_count: Mapped[int | None] = mapped_column(nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    job: Mapped[TranscriptionJob] = relationship(back_populates="transcription_result")
