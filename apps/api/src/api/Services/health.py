import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from transcribe_ai_shared import AsyncSessionFactory


class PostgresReadinessService:
    """Vérifie que PostgreSQL répond dans le délai accordé à la readiness."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        timeout_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds

    async def is_ready(self) -> bool:
        """Retourne ``False`` lorsqu'une connexion ou la requête de contrôle échoue."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._session_factory() as session:
                    await session.execute(text("SELECT 1"))
        except (TimeoutError, OSError, SQLAlchemyError):
            return False
        return True
