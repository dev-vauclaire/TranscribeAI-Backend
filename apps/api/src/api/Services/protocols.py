from collections.abc import Callable
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from transcribe_ai_shared import TranscriptionJob, TranscriptionResult


class JobCreationRepository(Protocol):
    """Partie du repository nécessaire à la création d'un job."""

    async def add(self, job: TranscriptionJob) -> None:
        """Ajoute le job à la transaction courante sans effectuer de commit."""
        ...

    async def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        """Relit le job pour résoudre l'issue potentiellement ambiguë d'un commit."""
        ...


JobRepositoryFactory: TypeAlias = Callable[[AsyncSession], JobCreationRepository]


class JobReadRepository(Protocol):
    """Partie du repository nécessaire à la consultation d'un job."""

    async def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        """Retourne le job correspondant ou ``None`` lorsqu'il est absent."""
        ...


class ResultReadRepository(Protocol):
    """Partie du repository nécessaire à la consultation d'un résultat."""

    async def get_by_job_uuid(
        self,
        job_uuid: UUID,
    ) -> TranscriptionResult | None:
        """Retourne le résultat unique du job ou ``None`` lorsqu'il est absent."""
        ...


JobReadRepositoryFactory: TypeAlias = Callable[[AsyncSession], JobReadRepository]
ResultRepositoryFactory: TypeAlias = Callable[[AsyncSession], ResultReadRepository]


class ReadinessService(Protocol):
    """Contrat du contrôle des dépendances indispensables à l'API."""

    async def is_ready(self) -> bool:
        """Indique si l'API peut traiter les requêtes dépendantes de PostgreSQL."""
        ...
