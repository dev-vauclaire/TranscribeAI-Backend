from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from transcribe_ai_shared.database.models import TranscriptionJob
from transcribe_ai_shared.database.models.enums import JobType
from transcribe_ai_shared.database.repositories import JobRepository
from transcribe_ai_shared.database.session import (
    AsyncSessionFactory,
    async_transaction,
)
from transcribe_ai_shared.storage.exceptions import InvalidAudioLocationError
from transcribe_ai_shared.storage.models import AudioLocation
from transcribe_ai_shared.worker.exceptions import WorkerJobTypeMismatchError
from transcribe_ai_shared.worker.models import ClaimedJob
from transcribe_ai_shared.worker.protocols import WorkerJobStore


class _WorkerJobRepository(Protocol):
    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> TranscriptionJob | None: ...

    async def renew_lease(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> bool: ...


_RepositoryFactory: TypeAlias = Callable[[AsyncSession], _WorkerJobRepository]


class PostgresWorkerJobStore(WorkerJobStore):
    """Adapte les opérations de lease à des transactions PostgreSQL courtes."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        expected_job_type: JobType,
        repository_factory: _RepositoryFactory = JobRepository,
    ) -> None:
        if not isinstance(expected_job_type, JobType):
            raise ValueError("expected_job_type doit être une valeur JobType")
        self._session_factory = session_factory
        self._expected_job_type = expected_job_type
        self._repository_factory = repository_factory

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> ClaimedJob | None:
        """Committe le claim avant de rendre un snapshot sans session au runtime."""
        async with async_transaction(self._session_factory) as session:
            repository = self._repository_factory(session)
            job = await repository.claim(
                job_uuid,
                worker_id,
                lease_expires_at,
                expected_attempt_count,
            )
            if job is None:
                return None
            if job.job_type is not self._expected_job_type:
                raise WorkerJobTypeMismatchError(
                    job.job_uuid,
                    self._expected_job_type,
                    job.job_type,
                )

            audio_location = AudioLocation(job.audio_uri)
            if audio_location.job_uuid != job.job_uuid:
                raise InvalidAudioLocationError(
                    "La localisation audio ne correspond pas au job réservé."
                )

            return ClaimedJob(
                job_uuid=job.job_uuid,
                job_type=job.job_type,
                attempt_count=job.attempt_count,
                audio_location=audio_location,
            )

    async def renew_lease(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> bool:
        """Committe un renouvellement isolé sans garder de transaction ouverte."""
        async with async_transaction(self._session_factory) as session:
            repository = self._repository_factory(session)
            return await repository.renew_lease(
                job_uuid,
                worker_id,
                lease_expires_at,
                expected_attempt_count,
            )
