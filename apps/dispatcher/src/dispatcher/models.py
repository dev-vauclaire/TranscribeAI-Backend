from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DispatchBatchResult:
    """Compte les issues observées pendant l'exécution d'un batch."""

    selected_count: int
    published_count: int
    confirmed_count: int
    stale_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class ExpiredJobSnapshot:
    """État observé lors de la sélection d'un job au lease expiré."""

    job_uuid: UUID
    attempt_count: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveryBatchResult:
    """Compte les issues observées pendant la récupération d'un batch."""

    selected_count: int
    requeued_count: int
    failed_count: int
    stale_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class DispatcherCycleResult:
    """Regroupe la récupération des leases et la publication d'un cycle."""

    recovery: RecoveryBatchResult
    dispatch: DispatchBatchResult
