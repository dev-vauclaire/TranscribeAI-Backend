from importlib import import_module
from pathlib import Path

from transcribe_ai_shared import AudioStorage, Transcriber

from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings


def create_whisperx_transcriber(
    *,
    settings: WorkerLongFormDiarizationSettings,
    storage: AudioStorage,
) -> Transcriber:
    """Charge les modèles une fois puis compose l'adapter WhisperX."""
    try:
        whisperx = import_module("whisperx")
        whisperx_diarize = import_module("whisperx.diarize")
    except ImportError as error:
        raise RuntimeError(
            "Les dépendances WhisperX sont absentes ; installez l'extra "
            "'cpu' ou 'gpu' du worker"
        ) from error

    from worker_long_form_diarization.transcribers.whisperx import (
        WhisperXDiarizationTranscriber,
    )

    model_directory = settings.worker_transcriber_model_directory
    whisper_directory = _create_cache_directory(model_directory / "whisperx")
    alignment_directory = _create_cache_directory(model_directory / "alignment")
    pyannote_directory = _create_cache_directory(model_directory / "pyannote")

    asr_model = whisperx.load_model(
        settings.worker_transcriber_model,
        settings.worker_transcriber_device,
        compute_type=settings.worker_transcriber_compute_type,
        language="fr",
        download_root=str(whisper_directory),
    )
    alignment_model, alignment_metadata = whisperx.load_align_model(
        language_code="fr",
        device=settings.worker_transcriber_device,
        model_dir=str(alignment_directory),
    )

    token = settings.worker_transcriber_hugging_face_token
    if token is None:  # Le modèle de settings protège déjà cet invariant.
        raise RuntimeError("Le token Hugging Face requis par WhisperX est absent")

    diarization_pipeline = whisperx_diarize.DiarizationPipeline(
        model_name=settings.worker_transcriber_diarization_model,
        token=token.get_secret_value(),
        device=settings.worker_transcriber_device,
        cache_dir=str(pyannote_directory),
    )

    return WhisperXDiarizationTranscriber(
        storage=storage,
        load_audio=whisperx.load_audio,
        asr_model=asr_model,
        align=whisperx.align,
        alignment_model=alignment_model,
        alignment_metadata=alignment_metadata,
        diarization_pipeline=diarization_pipeline,
        assign_word_speakers=whisperx.assign_word_speakers,
        device=settings.worker_transcriber_device,
        batch_size=settings.worker_transcriber_batch_size,
    )


def _create_cache_directory(path: Path) -> Path:
    """Crée un sous-cache persistant avant le chargement des modèles."""
    path.mkdir(parents=True, exist_ok=True)
    return path
