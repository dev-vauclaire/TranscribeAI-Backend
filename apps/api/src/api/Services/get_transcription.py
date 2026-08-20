from dataclasses import dataclass
import logging
from typing import Any
from uuid import UUID

from api.Services.protocols import (
    JobReadRepositoryFactory,
    ResultRepositoryFactory,
)
from api.exceptions import TranscriptionNotFoundError, TranscriptionQueryError
from transcribe_ai_shared import (
    AsyncSessionFactory,
    JobRepository,
    JobStatus,
    ResultRepository,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GetTranscriptionResult:
    """État public d'une transcription, détaché des modèles SQLAlchemy."""

    job_uuid: UUID
    status: JobStatus
    result: dict[str, Any] | None


class GetTranscriptionService:
    """Consulte le statut d'un job et charge son résultat uniquement au besoin."""

    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        job_repository_factory: JobReadRepositoryFactory = JobRepository,
        result_repository_factory: ResultRepositoryFactory = ResultRepository,
    ) -> None:
        self._session_factory = session_factory
        self._job_repository_factory = job_repository_factory
        self._result_repository_factory = result_repository_factory

    async def get(self, job_uuid: UUID) -> GetTranscriptionResult:
        """Retourne l'état demandé sans maintenir la session après le use case."""
        try:
            async with self._session_factory() as session:
                job_repository = self._job_repository_factory(session)
                job = await job_repository.get_by_uuid(job_uuid)
                if job is None:
                    raise TranscriptionNotFoundError(
                        "La transcription demandée n'existe pas."
                    )

                result_payload: dict[str, Any] | None = None
                if job.status is JobStatus.COMPLETED:
                    result_repository = self._result_repository_factory(session)
                    transcription_result = await result_repository.get_by_job_uuid(
                        job_uuid
                    )
                    if transcription_result is None:
                        logger.error(
                            "Le job COMPLETED %s ne possède aucun résultat.",
                            job_uuid,
                        )
                        raise TranscriptionQueryError(
                            "Le résultat durable de la transcription est absent."
                        )
                    result_payload = dict(transcription_result.result)

                return GetTranscriptionResult(
                    job_uuid=job.job_uuid,
                    status=job.status,
                    result=result_payload,
                )
        except (TranscriptionNotFoundError, TranscriptionQueryError):
            raise
        except Exception as error:
            logger.exception(
                "Impossible de consulter la transcription %s.",
                job_uuid,
            )
            raise TranscriptionQueryError(
                "La transcription n'a pas pu être consultée."
            ) from error
