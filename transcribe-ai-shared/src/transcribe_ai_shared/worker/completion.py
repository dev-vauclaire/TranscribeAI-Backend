from collections.abc import Callable
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from transcribe_ai_shared.database.models import TranscriptionResult
from transcribe_ai_shared.database.repositories import JobRepository, ResultRepository
from transcribe_ai_shared.database.session import (
    AsyncSessionFactory,
    async_transaction,
)
from transcribe_ai_shared.worker.exceptions import (
    TranscriptionCompletionError,
    TranscriptionCompletionRejectedError,
)
from transcribe_ai_shared.worker.models import ClaimedJob, TranscriptionOutput


class _CompletionJobRepository(Protocol):
    async def mark_completed(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> bool: ...


class _CompletionResultRepository(Protocol):
    async def add(self, result: TranscriptionResult) -> None: ...


_JobRepositoryFactory: TypeAlias = Callable[
    [AsyncSession],
    _CompletionJobRepository,
]
_ResultRepositoryFactory: TypeAlias = Callable[
    [AsyncSession],
    _CompletionResultRepository,
]


class TranscriptionCompletionService:
    """Persiste le résultat et clôture son job dans une transaction unique."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        job_repository_factory: _JobRepositoryFactory = JobRepository,
        result_repository_factory: _ResultRepositoryFactory = ResultRepository,
    ) -> None:
        self._session_factory = session_factory
        self._job_repository_factory = job_repository_factory
        self._result_repository_factory = result_repository_factory

    async def complete(
        self,
        *,
        job: ClaimedJob,
        worker_id: str,
        output: TranscriptionOutput,
    ) -> None:
        """Committe résultat et état, ou annule les deux si le claim est perdu."""
        try:
            async with async_transaction(self._session_factory) as session:
                result_repository = self._result_repository_factory(session)
                job_repository = self._job_repository_factory(session)

                await result_repository.add(
                    TranscriptionResult(
                        job_uuid=job.job_uuid,
                        result=output.result,
                    )
                )
                completed = await job_repository.mark_completed(
                    job.job_uuid,
                    worker_id,
                    job.attempt_count,
                )
                if not completed:
                    raise TranscriptionCompletionRejectedError(
                        job.job_uuid,
                        worker_id,
                        job.attempt_count,
                    )
        except TranscriptionCompletionRejectedError:
            raise
        except SQLAlchemyError as error:
            raise TranscriptionCompletionError(job.job_uuid) from error
