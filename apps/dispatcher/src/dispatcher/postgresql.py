from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from dispatcher.protocols import DispatchJobStore
from transcribe_ai_shared import (
    AsyncSessionFactory,
    JobRepository,
    JobStreamMessage,
    TranscriptionJob,
    async_transaction,
)


class _DispatchRepository(Protocol):
    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[TranscriptionJob]: ...

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        dispatched_at: datetime,
    ) -> bool: ...


_RepositoryFactory: TypeAlias = Callable[[AsyncSession], _DispatchRepository]


class PostgresDispatchJobStore(DispatchJobStore):
    """Adapte le repository SQLAlchemy aux frontières du dispatcher."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        repository_factory: _RepositoryFactory = JobRepository,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[JobStreamMessage]:
        """Détache les seules données utiles avant les appels réseau Redis."""
        async with self._session_factory() as session:
            repository = self._repository_factory(session)
            jobs = await repository.find_jobs_requiring_dispatch(limit)
            return [
                JobStreamMessage(
                    job_uuid=job.job_uuid,
                    job_type=job.job_type,
                    attempt_count=job.attempt_count,
                )
                for job in jobs
            ]

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        dispatched_at: datetime,
    ) -> bool:
        """Isole chaque confirmation dans sa propre transaction PostgreSQL."""
        async with async_transaction(self._session_factory) as session:
            repository = self._repository_factory(session)
            return await repository.mark_dispatched(
                job_uuid,
                expected_attempt_count,
                dispatched_at,
            )
