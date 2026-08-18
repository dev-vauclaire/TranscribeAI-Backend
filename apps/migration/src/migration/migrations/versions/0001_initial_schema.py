"""Create the initial transcription schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-18

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


job_status_enum = postgresql.ENUM(
    "QUEUED",
    "PROCESSING",
    "COMPLETED",
    "FAILED",
    name="transcription_job_status",
    create_type=False,
)
job_type_enum = postgresql.ENUM(
    "FAST",
    "BATCH",
    name="transcription_job_type",
    create_type=False,
)


def upgrade() -> None:
    """Create transcription jobs and transcription results."""
    bind = op.get_bind()
    job_status_enum.create(bind, checkfirst=False)
    job_type_enum.create(bind, checkfirst=False)

    op.create_table(
        "transcription_jobs",
        sa.Column("job_uuid", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            job_status_enum,
            server_default=sa.text("'QUEUED'::transcription_job_status"),
            nullable=False,
        ),
        sa.Column("job_type", job_type_enum, nullable=False),
        sa.Column("audio_uri", sa.Text(), nullable=False),
        sa.Column(
            "dispatch_required",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "last_dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "length(btrim(audio_uri)) > 0",
            name="audio_uri_non_empty",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_fields_consistent",
        ),
        sa.PrimaryKeyConstraint("job_uuid", name="pk_transcription_jobs"),
    )
    op.create_index(
        "idx_job_dispatch",
        "transcription_jobs",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'QUEUED' AND dispatch_required IS TRUE"),
    )
    op.create_index(
        "idx_job_expired_lease",
        "transcription_jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )

    op.create_table(
        "transcription_results",
        sa.Column("job_uuid", sa.Uuid(), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("speaker_count", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("model_version", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "speaker_count IS NULL OR speaker_count >= 0",
            name="speaker_count_non_negative",
        ),
        sa.CheckConstraint(
            "model_name IS NULL OR length(btrim(model_name)) > 0",
            name="model_name_non_empty",
        ),
        sa.CheckConstraint(
            "model_version IS NULL OR length(btrim(model_version)) > 0",
            name="model_version_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["job_uuid"],
            ["transcription_jobs.job_uuid"],
            name="fk_transcription_results_job_uuid_transcription_jobs",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_uuid", name="pk_transcription_results"),
    )


def downgrade() -> None:
    """Remove all transcription schema objects."""
    op.drop_table("transcription_results")
    op.drop_index(
        "idx_job_expired_lease",
        table_name="transcription_jobs",
    )
    op.drop_index("idx_job_dispatch", table_name="transcription_jobs")
    op.drop_table("transcription_jobs")

    bind = op.get_bind()
    job_type_enum.drop(bind, checkfirst=False)
    job_status_enum.drop(bind, checkfirst=False)
