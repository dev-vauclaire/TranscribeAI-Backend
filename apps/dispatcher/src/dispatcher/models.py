from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from transcribe_ai_shared import JobType


@dataclass(frozen=True, slots=True)
class DispatchJobSnapshot:
    """État PostgreSQL observé avant la publication d'un job."""

    job_uuid: UUID
    job_type: JobType
    attempt_count: int
    last_dispatched_at: datetime | None


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
class StaleDispatchSnapshot:
    """État observé d'une publication à réarmer sous contrôle CAS."""

    job_uuid: UUID
    attempt_count: int
    last_dispatched_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationBatchResult:
    """Compte les issues d'un batch de réconciliation des publications."""

    selected_count: int
    rearmed_count: int
    stale_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class DispatcherCycleResult:
    """Regroupe les trois phases ordonnées d'un cycle du dispatcher."""

    recovery: RecoveryBatchResult
    reconciliation: ReconciliationBatchResult
    dispatch: DispatchBatchResult
