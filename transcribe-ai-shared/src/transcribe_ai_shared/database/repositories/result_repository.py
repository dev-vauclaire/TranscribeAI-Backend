from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from transcribe_ai_shared.database.models import TranscriptionResult


class ResultRepository:
    """Accès asynchrones limités à la table ``transcription_results``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, result: TranscriptionResult) -> None:
        """Ajoute le résultat à la transaction courante sans la valider."""
        self._session.add(result)
        await self._session.flush()

    async def get_by_job_uuid(
        self,
        job_uuid: UUID,
    ) -> TranscriptionResult | None:
        """Recherche le résultat unique associé à la clé primaire du job."""
        return await self._session.get(TranscriptionResult, job_uuid)
