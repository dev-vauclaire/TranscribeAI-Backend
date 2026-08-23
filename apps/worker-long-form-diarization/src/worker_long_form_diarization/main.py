import asyncio
import logging
from typing import NoReturn

from worker_long_form_diarization.application import run
from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings
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


LOGGER = logging.getLogger(__name__)


def _create_transcriber(
    settings: WorkerLongFormDiarizationSettings,
    storage: AudioStorage,
) -> Transcriber:
    """Construit une seule instance du moteur configuré pour tout le processus."""
    if settings.worker_transcriber_backend == "whisperx":
        from worker_long_form_diarization.transcribers.whisperx_factory import (
            create_whisperx_transcriber,
        )

        return create_whisperx_transcriber(settings=settings, storage=storage)

    if settings.worker_environment != "development":
        raise RuntimeError("Le backend fake est réservé au développement")

    from transcribe_ai_shared.worker.testing import FakeTranscriber

    return FakeTranscriber()


def _log_result(result: WorkerProcessResult) -> None:
    """Journalise une itération sans exposer le résultat de transcription."""
    level = logging.DEBUG if isinstance(result, WorkerIdle) else logging.INFO
    LOGGER.log(
        level,
        "worker_long_form_diarization_iteration_completed result_type=%s",
        type(result).__name__,
    )


async def _run_from_environment() -> NoReturn:
    settings = WorkerLongFormDiarizationSettings()
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
    """Exécute le worker de transcription longue avec diarisation."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        asyncio.run(_run_from_environment())
    except KeyboardInterrupt:
        LOGGER.warning("worker_long_form_diarization_interrupted")
        return 130
    except Exception as error:
        LOGGER.error(
            "worker_long_form_diarization_failed error_type=%s",
            type(error).__name__,
        )
        return 1

    LOGGER.error("worker_long_form_diarization_stopped_unexpectedly")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
