from collections.abc import Callable
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from transcribe_ai_shared.database.models import JobStatus
from transcribe_ai_shared.database.repositories import JobRepository
from transcribe_ai_shared.database.session import (
    AsyncSessionFactory,
    async_transaction,
)
from transcribe_ai_shared.queue.models import MAX_ATTEMPT_COUNT
from transcribe_ai_shared.worker.exceptions import (
    TranscriptionFailureTransitionError,
    TranscriptionFailureTransitionRejectedError,
)
from transcribe_ai_shared.worker.models import (
    ClaimedJob,
    ClassifiedTranscriptionFailure,
    TranscriptionFailureCategory,
    TranscriptionFailureResolution,
)


class _FailureJobRepository(Protocol):
    async def requeue_after_failure(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
        error_code: str,
    ) -> bool: ...

    async def mark_failed(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
        error_code: str,
    ) -> bool: ...


_RepositoryFactory: TypeAlias = Callable[[AsyncSession], _FailureJobRepository]


class TranscriptionFailureService:
    """Résout un échec d'inférence dans une transaction PostgreSQL unique."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        max_attempts: int,
        repository_factory: _RepositoryFactory = JobRepository,
    ) -> None:
        if (
            type(max_attempts) is not int
            or max_attempts < 1
            or max_attempts > MAX_ATTEMPT_COUNT + 1
        ):
            raise ValueError("max_attempts doit être compris entre 1 et 2147483648")
        self._session_factory = session_factory
        self._max_attempts = max_attempts
        self._repository_factory = repository_factory

    async def handle(
        self,
        *,
        job: ClaimedJob,
        worker_id: str,
        failure: ClassifiedTranscriptionFailure,
    ) -> TranscriptionFailureResolution:
        """Committe une requeue ou un échec terminal pour la tentative détenue."""
        should_retry = (
            failure.category is TranscriptionFailureCategory.RETRYABLE
            and job.attempt_count + 1 < self._max_attempts
        )
        next_status = JobStatus.QUEUED if should_retry else JobStatus.FAILED
        next_attempt_count = (
            job.attempt_count + 1 if should_retry else job.attempt_count
        )

        try:
            async with async_transaction(self._session_factory) as session:
                repository = self._repository_factory(session)
                if should_retry:
                    transitioned = await repository.requeue_after_failure(
                        job.job_uuid,
                        worker_id,
                        job.attempt_count,
                        failure.error_code,
                    )
                else:
                    transitioned = await repository.mark_failed(
                        job.job_uuid,
                        worker_id,
                        job.attempt_count,
                        failure.error_code,
                    )

                if not transitioned:
                    raise TranscriptionFailureTransitionRejectedError(
                        job.job_uuid,
                        worker_id,
                        job.attempt_count,
                    )
        except TranscriptionFailureTransitionRejectedError:
            raise
        except SQLAlchemyError as error:
            raise TranscriptionFailureTransitionError(job.job_uuid) from error

        return TranscriptionFailureResolution(
            status=next_status,
            attempt_count=next_attempt_count,
        )
