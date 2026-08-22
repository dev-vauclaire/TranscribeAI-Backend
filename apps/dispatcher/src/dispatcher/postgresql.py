from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from dispatcher.models import ExpiredJobSnapshot
from dispatcher.protocols import DispatchJobStore, ExpiredJobStore
from transcribe_ai_shared import (
    AsyncSessionFactory,
    JobRepository,
    JobStreamMessage,
    TranscriptionJob,
    async_transaction,
)


class _DispatcherRepository(Protocol):
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

    async def find_expired_processing_jobs(
        self,
        limit: int,
    ) -> list[TranscriptionJob]: ...

    async def recover_expired_job(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        expected_lease_expires_at: datetime,
        *,
        should_retry: bool,
    ) -> bool: ...


_RepositoryFactory: TypeAlias = Callable[[AsyncSession], _DispatcherRepository]


class PostgresDispatchJobStore(DispatchJobStore, ExpiredJobStore):
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

    async def find_expired_jobs(
        self,
        limit: int,
    ) -> list[ExpiredJobSnapshot]:
        """Détache les gardes nécessaires avant les transitions de recovery."""
        async with self._session_factory() as session:
            repository = self._repository_factory(session)
            jobs = await repository.find_expired_processing_jobs(limit)
            snapshots: list[ExpiredJobSnapshot] = []
            for job in jobs:
                if job.lease_expires_at is None:
                    raise ValueError(
                        "un job PROCESSING expiré doit posséder une échéance de lease"
                    )
                snapshots.append(
                    ExpiredJobSnapshot(
                        job_uuid=job.job_uuid,
                        attempt_count=job.attempt_count,
                        lease_expires_at=job.lease_expires_at,
                    )
                )
            return snapshots

    async def recover_expired_job(
        self,
        snapshot: ExpiredJobSnapshot,
        *,
        should_retry: bool,
    ) -> bool:
        """Isole le CAS d'un snapshot expiré dans sa propre transaction."""
        async with async_transaction(self._session_factory) as session:
            repository = self._repository_factory(session)
            return await repository.recover_expired_job(
                snapshot.job_uuid,
                snapshot.attempt_count,
                snapshot.lease_expires_at,
                should_retry=should_retry,
            )
