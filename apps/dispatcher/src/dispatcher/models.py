from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DispatchBatchResult:
    """Compte les issues observées pendant l'exécution d'un batch."""

    selected_count: int
    published_count: int
    confirmed_count: int
    stale_count: int
    error_count: int
