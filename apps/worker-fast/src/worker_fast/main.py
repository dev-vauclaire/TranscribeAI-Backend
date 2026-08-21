import asyncio
import logging
from typing import NoReturn

from worker_fast.application import run
from worker_fast.config import WorkerFastSettings
from transcribe_ai_shared import (
    DatabaseSettings,
    RedisSettings,
    Transcriber,
    WorkerIdle,
    WorkerProcessResult,
)


LOGGER = logging.getLogger(__name__)


def _create_transcriber(settings: WorkerFastSettings) -> Transcriber:
    """Construit uniquement le fake explicitement autorisé pour le développement."""
    if settings.worker_transcriber_backend != "fake":
        raise RuntimeError("Aucun backend de transcription FAST n'est configuré")
    if settings.worker_environment != "development":
        raise RuntimeError("Le backend fake est réservé au développement")

    from transcribe_ai_shared.worker.testing import FakeTranscriber

    return FakeTranscriber()


def _log_result(result: WorkerProcessResult) -> None:
    """Journalise une itération sans exposer le résultat de transcription."""
    level = logging.DEBUG if isinstance(result, WorkerIdle) else logging.INFO
    LOGGER.log(
        level,
        "worker_fast_iteration_completed result_type=%s",
        type(result).__name__,
    )


async def _run_from_environment() -> NoReturn:
    settings = WorkerFastSettings()
    await run(
        database_settings=DatabaseSettings(),
        redis_settings=RedisSettings(),
        worker_settings=settings,
        transcriber=_create_transcriber(settings),
        on_result=_log_result,
    )


def main() -> int:
    """Exécute le worker FAST jusqu'à son interruption ou une erreur."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        LOGGER.warning("worker_fast_interrupted")
        return 130
    except Exception as error:
        LOGGER.error("worker_fast_failed error_type=%s", type(error).__name__)
        return 1

    LOGGER.error("worker_fast_stopped_unexpectedly")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
