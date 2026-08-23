import asyncio
import logging
from math import isfinite

import redis.asyncio as redis_asyncio
from sqlalchemy import text

from transcribe_ai_shared.database.config import DatabaseSettings
from transcribe_ai_shared.database.engine import create_async_db_engine
from transcribe_ai_shared.observability import configure_logging, log_event
from transcribe_ai_shared.queue.config import RedisSettings


LOGGER = logging.getLogger(__name__)
SERVICE = "worker-healthcheck"
DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS = 5.0


def _create_redis_client(
    settings: RedisSettings,
    timeout_seconds: float,
) -> redis_asyncio.Redis:
    """Construit le client dédié à une sonde courte et sans pool persistant."""
    return redis_asyncio.Redis.from_url(
        str(settings.redis_url),
        decode_responses=False,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )


async def check_worker_dependencies(
    database_settings: DatabaseSettings,
    redis_settings: RedisSettings,
    *,
    timeout_seconds: float = DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS,
) -> None:
    """Vérifie les dépendances indispensables aux workers puis les referme.

    Le timeout borne ensemble le ``SELECT 1`` PostgreSQL et le ``PING`` Redis.
    Une exception indique une sonde négative à l'appelant ; aucun détail de
    connexion n'est journalisé à ce niveau.
    """
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds doit être fini et strictement positif")

    engine = create_async_db_engine(database_settings)
    try:
        redis_client = _create_redis_client(redis_settings, timeout_seconds)
        try:
            async with asyncio.timeout(timeout_seconds):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))

                if not await redis_client.ping():
                    raise RuntimeError("Redis n'a pas confirmé la commande PING")
        finally:
            await redis_client.aclose()
    finally:
        await engine.dispose()


async def _run_from_environment() -> None:
    await check_worker_dependencies(
        database_settings=DatabaseSettings(),
        redis_settings=RedisSettings(),
    )


def main() -> int:
    """Retourne un statut exploitable par HEALTHCHECK sans exposer de secret."""
    configure_logging(service=SERVICE)
    try:
        asyncio.run(_run_from_environment())
    except Exception as error:
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE,
            event="worker_dependency_healthcheck_failed",
            error_type=type(error).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
