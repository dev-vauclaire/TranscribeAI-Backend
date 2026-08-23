import asyncio
from importlib import import_module
import logging
from typing import NoReturn

from worker_fast.application import run
from worker_fast.config import WorkerFastSettings
from worker_fast.transcribers import FasterWhisperTranscriber
from transcribe_ai_shared import (
    AudioStorage,
    DatabaseSettings,
    FileSystemAudioStorage,
    RedisSettings,
    StorageSettings,
    Transcriber,
    WorkerIdle,
    WorkerProcessResult,
)
from transcribe_ai_shared.observability import configure_logging, log_event


LOGGER = logging.getLogger(__name__)
SERVICE = "worker-fast"


def _create_transcriber(
    settings: WorkerFastSettings,
    storage: AudioStorage,
) -> Transcriber:
    """Construit une seule instance du moteur configuré pour tout le processus."""
    if settings.worker_transcriber_backend == "faster-whisper":
        try:
            faster_whisper = import_module("faster_whisper")
        except ImportError as error:
            raise RuntimeError(
                "La dépendance Faster-Whisper est absente ; installez l'extra "
                "'cpu' ou 'gpu' du worker"
            ) from error

        model = faster_whisper.WhisperModel(
            settings.worker_transcriber_model,
            device=settings.worker_transcriber_device,
            compute_type=settings.worker_transcriber_compute_type,
        )
        return FasterWhisperTranscriber(model=model, storage=storage)

    if settings.worker_environment != "development":
        raise RuntimeError("Le backend fake est réservé au développement")

    from transcribe_ai_shared.worker.testing import FakeTranscriber

    return FakeTranscriber()


def _log_result(result: WorkerProcessResult) -> None:
    """Journalise une itération sans exposer le résultat de transcription."""
    level = logging.DEBUG if isinstance(result, WorkerIdle) else logging.INFO
    message = None if isinstance(result, WorkerIdle) else result.message
    log_event(
        LOGGER,
        level,
        service=SERVICE,
        event="worker_iteration_completed",
        job_uuid=message.job_uuid if message is not None else None,
        attempt_count=message.attempt_count if message is not None else None,
        redis_message_id=(message.redis_message_id if message is not None else None),
        result_type=type(result).__name__,
    )


async def _run_from_environment() -> NoReturn:
    settings = WorkerFastSettings()
    storage_settings = StorageSettings()
    storage = FileSystemAudioStorage(storage_settings.audio_storage_path)
    await run(
        database_settings=DatabaseSettings(),
        redis_settings=RedisSettings(),
        worker_settings=settings,
        transcriber=_create_transcriber(settings, storage),
        on_result=_log_result,
    )


def main() -> int:
    """Exécute le worker FAST jusqu'à son interruption ou une erreur."""
    configure_logging(service=SERVICE)

    try:
        asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        log_event(
            LOGGER,
            logging.WARNING,
            service=SERVICE,
            event="worker_interrupted",
        )
        return 130
    except Exception as error:
        log_event(
            LOGGER,
            logging.ERROR,
            service=SERVICE,
            event="worker_process_failed",
            error_type=type(error).__name__,
        )
        return 1

    log_event(
        LOGGER,
        logging.ERROR,
        service=SERVICE,
        event="worker_stopped_unexpectedly",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
