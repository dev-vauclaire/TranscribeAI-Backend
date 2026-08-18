from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from transcribe_ai_shared.database.base import Base
from transcribe_ai_shared.database.models.enums import (
    JobStatus,
    JobType,
    enum_values,
)

if TYPE_CHECKING:
    from transcribe_ai_shared.database.models.transcription_result import (
        TranscriptionResult,
    )


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_non_negative",
        ),
        CheckConstraint(
            "length(btrim(audio_uri)) > 0",
            name="audio_uri_non_empty",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_fields_consistent",
        ),
        Index(
            "idx_job_dispatch",
            "created_at",
            postgresql_where=text("status = 'QUEUED' AND dispatch_required IS TRUE"),
        ),
        Index(
            "idx_job_expired_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
    )

    job_uuid: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(
            JobStatus,
            name="transcription_job_status",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    job_type: Mapped[JobType] = mapped_column(
        SqlEnum(
            JobType,
            name="transcription_job_type",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    audio_uri: Mapped[str] = mapped_column(Text, nullable=False)
    dispatch_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    transcription_result: Mapped[TranscriptionResult | None] = relationship(
        back_populates="job",
        passive_deletes="all",
        uselist=False,
    )
