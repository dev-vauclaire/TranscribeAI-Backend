from datetime import datetime
from typing import Protocol
from uuid import UUID

from dispatcher.models import ExpiredJobSnapshot
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


class ExpiredJobStore(Protocol):
    """Port PostgreSQL nécessaire à la récupération des leases expirés."""

    async def find_expired_jobs(
        self,
        limit: int,
    ) -> list[ExpiredJobSnapshot]:
        """Retourne un snapshot borné des jobs expirés selon PostgreSQL."""
        ...

    async def recover_expired_job(
        self,
        snapshot: ExpiredJobSnapshot,
        *,
        should_retry: bool,
    ) -> bool:
        """Applique la transition si le lease observé est toujours expiré."""
        ...
