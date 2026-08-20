from typing import Protocol

from transcribe_ai_shared.database.models.enums import JobType
from transcribe_ai_shared.queue.models import (
    AutoClaimResult,
    JobStreamMessage,
    ReceivedJobStreamMessage,
)


class TranscriptionStreams(Protocol):
    """Primitives Redis Streams nécessaires au dispatcher et aux workers."""

    async def aclose(self) -> None:
        """Libère le pool de connexions détenu par l'adaptateur."""
        ...

    async def publish(self, message: JobStreamMessage) -> str:
        """Publie un job dans le stream déterminé par son type."""
        ...

    async def ensure_consumer_group(
        self,
        job_type: JobType,
        group_name: str,
    ) -> None:
        """Crée de manière idempotente le groupe et son stream."""
        ...

    async def consume(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        block_milliseconds: int | None = 5_000,
    ) -> ReceivedJobStreamMessage | None:
        """Consomme au plus un nouveau message et le place dans le PEL."""
        ...

    async def ack_and_delete(
        self,
        group_name: str,
        message: ReceivedJobStreamMessage,
    ) -> bool:
        """Acquitte puis supprime atomiquement un message possédé par le groupe."""
        ...

    async def autoclaim(
        self,
        job_type: JobType,
        group_name: str,
        consumer_name: str,
        *,
        min_idle_milliseconds: int,
        start_id: str = "0-0",
        count: int = 1,
    ) -> AutoClaimResult:
        """Transfère des messages pending sans appliquer de décision métier."""
        ...
