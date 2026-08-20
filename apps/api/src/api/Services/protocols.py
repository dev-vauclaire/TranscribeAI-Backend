from collections.abc import Callable
from typing import Protocol, TypeAlias
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from transcribe_ai_shared import TranscriptionJob


class JobCreationRepository(Protocol):
    """Partie du repository nécessaire à la création d'un job."""

    async def add(self, job: TranscriptionJob) -> None:
        """Ajoute le job à la transaction courante sans effectuer de commit."""
        ...

    async def get_by_uuid(self, job_uuid: UUID) -> TranscriptionJob | None:
        """Relit le job pour résoudre l'issue potentiellement ambiguë d'un commit."""
        ...


JobRepositoryFactory: TypeAlias = Callable[[AsyncSession], JobCreationRepository]
