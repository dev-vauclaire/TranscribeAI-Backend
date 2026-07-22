from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_ai_shared.database.models.job_model import Job, JobStatus


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # Insère un nouveau job
    def add(self, job: Job) -> Job:
        self._session.add(job)
        self._session.flush()
        return job

    # Récupère un job par son ID
    def get_by_id(self, job_id: int) -> Job | None:
        return self._session.get(Job, job_id)

    # Récupère un job par son UUID
    def get_by_uuid(self, job_uuid: UUID) -> Job | None:
        statement = select(Job).where(Job.uuid == job_uuid)
        return self._session.scalar(statement)

    # Change le status d'un job en particulier
    def update_status(self, job_id: int, status: JobStatus) -> Job | None:
        job = self.get_by_id(job_id)
        if not job:
            return None
        job.status = status
        self._session.flush()
        return job

    # Finalise le job en stockant le résultat (JSON) quelle que soit sa nature.
    def complete_job(self, job_id: int, result_data: dict[str, Any]) -> Job | None:
        job = self.get_by_id(job_id)
        if not job:
            return None
        job.status = JobStatus.COMPLETED
        job.result = result_data
        job.ended_at = datetime.now(timezone.utc)
        self._session.flush()
        return job

    # Supprime un job une fois qu'il est completed
    def delete_job(self, job_id: int) -> bool:
        job = self.get_by_id(job_id)
        if not job or job.status in {JobStatus.PROCESSING, JobStatus.PENDING}:
            return False

        self._session.delete(job)
        self._session.flush()
        return True

    # Marque le job comme failed en ajoutant un message d'erreur
    def fail_job(
        self,
        job_id: int,
        error_msg: str = "Une erreur est survenue",
        ended_at: datetime | None = None,
    ) -> Job | None:
        job = self.get_by_id(job_id)
        if not job:
            return None
        job.status = JobStatus.FAILED
        job.ended_at = ended_at
        job.error_message = error_msg
        self._session.flush()
        return job

    # Récupère une liste de jobs par status avec pagination
    def list_by_status(
        self,
        status: JobStatus,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Job]:
        statement = (
            select(Job)
            .where(Job.status == status)
            .order_by(Job.created_at.asc(), Job.id.asc())
            .offset(offset)
            .limit(limit)
        )

        return self._session.scalars(statement).all()
