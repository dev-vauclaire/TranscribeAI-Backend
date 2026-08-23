"""Add the index used to reconcile stale dispatch confirmations.

Revision ID: 0002_add_stale_dispatch_index
Revises: 0001_initial_schema
Create Date: 2026-08-23

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_stale_dispatch_index"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Index stale QUEUED jobs whose Redis dispatch may need rearming."""
    op.create_index(
        "idx_job_stale_dispatch",
        "transcription_jobs",
        ["last_dispatched_at", "job_uuid"],
        unique=False,
        postgresql_where=sa.text(
            "status = 'QUEUED' AND dispatch_required IS FALSE "
            "AND last_dispatched_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """Remove the stale dispatch reconciliation index."""
    op.drop_index(
        "idx_job_stale_dispatch",
        table_name="transcription_jobs",
    )
