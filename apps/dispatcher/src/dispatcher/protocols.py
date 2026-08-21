from datetime import datetime
from typing import Protocol
from uuid import UUID

from transcribe_ai_shared import JobStreamMessage


class DispatchJobStore(Protocol):
    """Port PostgreSQL nécessaire au service de dispatch."""

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[JobStreamMessage]:
        """Retourne un snapshot des jobs à publier, sans conserver de session."""
        ...

    async def mark_dispatched(
        self,
        job_uuid: UUID,
        expected_attempt_count: int,
        dispatched_at: datetime,
    ) -> bool:
        """Confirme la publication par comparaison avec la tentative observée."""
        ...
