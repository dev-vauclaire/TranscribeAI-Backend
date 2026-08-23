from typing import Protocol

from dispatcher.models import (
    DispatchJobSnapshot,
    ExpiredJobSnapshot,
    StaleDispatchSnapshot,
)


class DispatchJobStore(Protocol):
    """Port PostgreSQL nécessaire au service de dispatch."""

    async def find_jobs_requiring_dispatch(
        self,
        limit: int,
    ) -> list[DispatchJobSnapshot]:
        """Retourne un snapshot des jobs à publier, sans conserver de session."""
        ...

    async def mark_dispatched(
        self,
        snapshot: DispatchJobSnapshot,
    ) -> bool:
        """Confirme la publication par comparaison avec le snapshot observé."""
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


class DispatchReconciliationStore(Protocol):
    """Port PostgreSQL nécessaire à la réconciliation des publications."""

    async def find_stale_dispatched_jobs(
        self,
        limit: int,
        reconciliation_timeout_seconds: int,
    ) -> list[StaleDispatchSnapshot]:
        """Liste un batch de publications anciennes selon l'horloge PostgreSQL."""
        ...

    async def rearm_stale_dispatch(
        self,
        snapshot: StaleDispatchSnapshot,
        reconciliation_timeout_seconds: int,
    ) -> bool:
        """Réarme la publication si le snapshot est toujours obsolète."""
        ...
