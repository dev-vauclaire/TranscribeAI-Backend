from enum import StrEnum


class JobType(StrEnum):
    FAST = "FAST"
    BATCH = "BATCH"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Return enum values for SQLAlchemy native enum persistence."""
    return [member.value for member in enum_class]
