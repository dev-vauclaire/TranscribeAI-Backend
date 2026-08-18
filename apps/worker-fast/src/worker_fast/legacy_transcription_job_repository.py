"""Compatibilité temporaire du worker synchrone pendant la refonte des repositories."""

from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ai_shared import (
    JobStatus,
    TranscriptionJob,
    TranscriptionResult,
)


class LegacyTranscriptionJobRepository:
    """Préserve le worker existant jusqu'à sa migration asynchrone."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, job: TranscriptionJob) -> TranscriptionJob:
        self._session.add(job)
        self._session.flush()
        return job

    def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        return self._session.get(TranscriptionJob, job_uuid)

    def _get_by_uuid_for_update(
        self,
        job_uuid: UUID,
    ) -> TranscriptionJob | None:
        statement = (
            select(TranscriptionJob)
            .where(TranscriptionJob.job_uuid == job_uuid)
            .with_for_update()
        )
        return self._session.scalar(statement)

    def update_status(
        self,
        job_uuid: UUID,
        status: JobStatus,
    ) -> TranscriptionJob | None:
        job = self._get_by_uuid_for_update(job_uuid)
        if job is None:
            return None

        job.status = status
        if status is JobStatus.PROCESSING:
            if job.started_at is None:
                job.started_at = datetime.now(timezone.utc)
            job.attempt_count += 1
        self._session.flush()
        return job

    def complete_job(
        self,
        job_uuid: UUID,
        result_data: dict[str, Any],
        *,
        speaker_count: int | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        completed_at: datetime | None = None,
    ) -> TranscriptionJob | None:
        job = self._get_by_uuid_for_update(job_uuid)
        if job is None:
            return None

        job.status = JobStatus.COMPLETED
        if job.completed_at is None:
            job.completed_at = completed_at or datetime.now(timezone.utc)
        job.last_error = None
        job.lease_owner = None
        job.lease_expires_at = None
        result = self._session.get(TranscriptionResult, job_uuid)
        if result is None:
            result = TranscriptionResult(
                job=job,
                result=result_data,
                speaker_count=speaker_count,
                model_name=model_name,
                model_version=model_version,
            )
            self._session.add(result)
        else:
            result.result = result_data
            result.speaker_count = speaker_count
            result.model_name = model_name
            result.model_version = model_version
        self._session.flush()
        return job

    def fail_job(
        self,
        job_uuid: UUID,
        error_message: str = "Une erreur est survenue",
        completed_at: datetime | None = None,
    ) -> TranscriptionJob | None:
        job = self._get_by_uuid_for_update(job_uuid)
        if job is None:
            return None

        job.status = JobStatus.FAILED
        job.completed_at = completed_at or datetime.now(timezone.utc)
        job.last_error = error_message
        job.lease_owner = None
        job.lease_expires_at = None
        self._session.flush()
        return job

    def list_by_status(
        self,
        status: JobStatus,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[TranscriptionJob]:
        statement = (
            select(TranscriptionJob)
            .where(TranscriptionJob.status == status)
            .order_by(
                TranscriptionJob.created_at.asc(),
                TranscriptionJob.job_uuid.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return self._session.scalars(statement).all()
