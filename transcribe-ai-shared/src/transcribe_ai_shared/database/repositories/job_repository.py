from collections.abc import Collection
from datetime import datetime
from itertools import batched
from uuid import UUID

from sqlalchemy import exists, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from transcribe_ai_shared.database.models import (
    JobStatus,
    TranscriptionJob,
    TranscriptionResult,
)


_JOB_UUID_BATCH_SIZE = 1_000


def _literal_job_status(status: JobStatus) -> ColumnElement[JobStatus]:
    """Intègre un statut constant afin que PostgreSQL puisse prouver le prédicat partiel."""
    return literal(
        status,
        type_=TranscriptionJob.status.type,
        literal_execute=True,
    )


class JobRepository:
    """Accès asynchrones limités à la table ``transcription_jobs``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: TranscriptionJob) -> None:
        """Ajoute le job à la transaction courante sans la valider."""
        self._session.add(job)
        await self._session.flush()

    async def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        """Recherche un job par sa clé primaire."""
        return await self._session.get(TranscriptionJob, job_uuid)

    async def get_jobs_by_uuids(
        self,
        job_uuids: Collection[UUID],
    ) -> list[TranscriptionJob]:
        """Charge les jobs existants par lots, sans garantir leur ordre.

        Les UUID dupliqués sont ignorés et une erreur sur un lot est propagée :
        l'appelant ne peut donc pas confondre un résultat partiel avec des jobs
        absents de PostgreSQL.
        """
        unique_job_uuids = tuple(dict.fromkeys(job_uuids))
        if not unique_job_uuids:
            return []

        jobs: list[TranscriptionJob] = []
        for job_uuid_batch in batched(unique_job_uuids, _JOB_UUID_BATCH_SIZE):
            statement = select(TranscriptionJob).where(
                TranscriptionJob.job_uuid.in_(job_uuid_batch)
            )
            result = await self._session.scalars(statement)
            jobs.extend(result.all())

        return jobs

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[TranscriptionJob]:
        """Liste sans verrou les jobs à publier, dans un ordre stable.

        Plusieurs dispatchers peuvent lire le même job : les doublons Redis
        sont volontairement neutralisés plus tard par le claim PostgreSQL.
        """
        statement = (
            select(TranscriptionJob)
            .where(
                TranscriptionJob.status == _literal_job_status(JobStatus.QUEUED),
                TranscriptionJob.dispatch_required.is_(True),
            )
            .order_by(
                TranscriptionJob.created_at.asc(),
                TranscriptionJob.job_uuid.asc(),
            )
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        dispatched_at: datetime,
    ) -> bool:
        """Confirme par CAS la publication de la tentative attendue.

        Le worker peut passer le job à ``PROCESSING`` entre la publication Redis
        et cette mise à jour PostgreSQL : le statut n'est donc pas filtré. La
        garde sur ``attempt_count`` empêche une confirmation retardée d'annuler
        le réarmement créé par une requeue. ``False`` indique que le job est
        absent, déjà confirmé ou passé à une tentative plus récente.
        """
        statement = (
            update(TranscriptionJob)
            .where(
                TranscriptionJob.job_uuid == job_uuid,
                TranscriptionJob.dispatch_required.is_(True),
                TranscriptionJob.attempt_count == expected_attempt_count,
            )
            .values(
                dispatch_required=False,
                last_dispatched_at=dispatched_at,
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def claim(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> TranscriptionJob | None:
        """Réserve atomiquement la tentative encore attendue par le worker."""
        statement = (
            update(TranscriptionJob)
            .where(
                TranscriptionJob.job_uuid == job_uuid,
                TranscriptionJob.status == JobStatus.QUEUED,
                TranscriptionJob.attempt_count == expected_attempt_count,
            )
            .values(
                status=JobStatus.PROCESSING,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                started_at=func.coalesce(TranscriptionJob.started_at, func.now()),
            )
            .returning(TranscriptionJob)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def renew_lease(
        self,
        job_uuid: UUID,
        worker_id: str,
        lease_expires_at: datetime,
        expected_attempt_count: int,
    ) -> bool:
        """Prolonge uniquement la tentative au lease encore valide.

        Un worker retardé ne peut pas ressusciter un lease expiré. Une horloge
        locale en recul ne raccourcit pas non plus l'échéance déjà persistée.
        """
        statement = (
            update(TranscriptionJob)
            .where(
                TranscriptionJob.job_uuid == job_uuid,
                TranscriptionJob.status == JobStatus.PROCESSING,
                TranscriptionJob.lease_owner == worker_id,
                TranscriptionJob.attempt_count == expected_attempt_count,
                TranscriptionJob.lease_expires_at > func.now(),
            )
            .values(
                lease_expires_at=func.greatest(
                    TranscriptionJob.lease_expires_at,
                    lease_expires_at,
                )
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def find_expired_processing_jobs(
        self,
        now: datetime,
        limit: int,
    ) -> list[TranscriptionJob]:
        """Liste les jobs en cours dont le lease est expiré, du plus ancien au plus récent."""
        statement = (
            select(TranscriptionJob)
            .where(
                TranscriptionJob.status == _literal_job_status(JobStatus.PROCESSING),
                TranscriptionJob.lease_expires_at < now,
            )
            .order_by(
                TranscriptionJob.lease_expires_at.asc(),
                TranscriptionJob.job_uuid.asc(),
            )
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def requeue_expired_job(
        self,
        job_uuid: UUID,
    ) -> TranscriptionJob | None:
        """Réarme le dispatch d'un job expiré et compte sa reprise worker."""
        statement = (
            update(TranscriptionJob)
            .where(
                TranscriptionJob.job_uuid == job_uuid,
                TranscriptionJob.status == JobStatus.PROCESSING,
                TranscriptionJob.lease_expires_at < func.now(),
            )
            .values(
                status=JobStatus.QUEUED,
                dispatch_required=True,
                lease_owner=None,
                lease_expires_at=None,
                attempt_count=TranscriptionJob.attempt_count + 1,
            )
            .returning(TranscriptionJob)
            .execution_options(populate_existing=True)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def mark_failed(
        self,
        job_uuid: UUID,
        worker_id: str,
        error_code: str,
    ) -> bool:
        """Termine en échec uniquement le job détenu par le worker indiqué."""
        statement = (
            update(TranscriptionJob)
            .where(
                TranscriptionJob.job_uuid == job_uuid,
                TranscriptionJob.status == JobStatus.PROCESSING,
                TranscriptionJob.lease_owner == worker_id,
            )
            .values(
                status=JobStatus.FAILED,
                last_error=error_code,
                completed_at=func.now(),
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def mark_completed(
        self,
        job_uuid: UUID,
        worker_id: str,
        expected_attempt_count: int,
    ) -> bool:
        """Termine la tentative détenue seulement si son résultat existe.

        La vérification du résultat protège l'invariant applicatif selon lequel
        un job ``COMPLETED`` possède son unique résultat durable. L'appelant
        doit donc insérer ce résultat dans la même transaction avant l'UPDATE.
        """
        statement = (
            update(TranscriptionJob)
            .where(
                TranscriptionJob.job_uuid == job_uuid,
                TranscriptionJob.status == JobStatus.PROCESSING,
                TranscriptionJob.lease_owner == worker_id,
                TranscriptionJob.attempt_count == expected_attempt_count,
                exists().where(
                    TranscriptionResult.job_uuid == TranscriptionJob.job_uuid
                ),
            )
            .values(
                status=JobStatus.COMPLETED,
                completed_at=func.now(),
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1
