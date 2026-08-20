from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from transcribe_ai_shared.database.models.enums import JobType
from transcribe_ai_shared.queue.exceptions import InvalidJobStreamMessageError


MAX_ATTEMPT_COUNT = 2_147_483_647


class TranscriptionStreamName(StrEnum):
    """Noms stables des streams de transcription."""

    FAST = "transcription:fast"
    BATCH = "transcription:batch"


def stream_name_for_job_type(job_type: JobType) -> TranscriptionStreamName:
    """Sélectionne le stream à partir du type métier, sans chaîne arbitraire."""
    if not isinstance(job_type, JobType):
        raise ValueError("job_type doit être une valeur JobType")

    match job_type:
        case JobType.FAST:
            return TranscriptionStreamName.FAST
        case JobType.BATCH:
            return TranscriptionStreamName.BATCH

    raise ValueError("job_type ne correspond à aucun stream de transcription")


def _validate_job_fields(
    job_uuid: UUID,
    job_type: JobType,
    attempt_count: int,
    *,
    redis_message_id: str | None = None,
) -> None:
    if not isinstance(job_uuid, UUID):
        raise InvalidJobStreamMessageError(
            "job_uuid doit être un UUID",
            redis_message_id=redis_message_id,
        )
    if not isinstance(job_type, JobType):
        raise InvalidJobStreamMessageError(
            "job_type doit être une valeur JobType",
            redis_message_id=redis_message_id,
        )
    if type(attempt_count) is not int or not (0 <= attempt_count <= MAX_ATTEMPT_COUNT):
        raise InvalidJobStreamMessageError(
            "attempt_count doit être un entier compris entre 0 et 2147483647",
            redis_message_id=redis_message_id,
        )


def _validate_redis_message_id(redis_message_id: str) -> None:
    if not isinstance(redis_message_id, str):
        raise InvalidJobStreamMessageError(
            "redis_message_id doit être une chaîne",
        )

    timestamp, separator, sequence = redis_message_id.partition("-")
    if (
        separator != "-"
        or not timestamp.isascii()
        or not sequence.isascii()
        or not timestamp.isdecimal()
        or not sequence.isdecimal()
    ):
        raise InvalidJobStreamMessageError(
            "redis_message_id doit être un identifiant Redis concret",
            redis_message_id=redis_message_id,
        )


@dataclass(frozen=True, slots=True)
class JobStreamMessage:
    """Message métier prêt à être publié dans le stream de son type."""

    job_uuid: UUID
    job_type: JobType
    attempt_count: int

    def __post_init__(self) -> None:
        _validate_job_fields(self.job_uuid, self.job_type, self.attempt_count)


@dataclass(frozen=True, slots=True)
class ReceivedJobStreamMessage:
    """Message consommé enrichi de son identifiant et de son stream Redis."""

    redis_message_id: str
    job_uuid: UUID
    job_type: JobType
    attempt_count: int

    def __post_init__(self) -> None:
        _validate_redis_message_id(self.redis_message_id)
        _validate_job_fields(
            self.job_uuid,
            self.job_type,
            self.attempt_count,
            redis_message_id=self.redis_message_id,
        )


@dataclass(frozen=True, slots=True)
class AutoClaimResult:
    """Résultat normalisé d'un parcours Redis ``XAUTOCLAIM``."""

    next_start_id: str
    messages: tuple[ReceivedJobStreamMessage, ...]
    deleted_message_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_redis_message_id(self.next_start_id)
        if not isinstance(self.messages, tuple) or not all(
            isinstance(message, ReceivedJobStreamMessage) for message in self.messages
        ):
            raise TypeError("messages doit être un tuple de messages Redis reçus")
        if not isinstance(self.deleted_message_ids, tuple):
            raise TypeError("deleted_message_ids doit être un tuple")
        for redis_message_id in self.deleted_message_ids:
            _validate_redis_message_id(redis_message_id)
