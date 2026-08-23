from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import worker_long_form_diarization.transcribers.whisperx as adapter_module
import worker_long_form_diarization.transcribers.whisperx_factory as factory_module
from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings


pytestmark = pytest.mark.unit


def test_factory_loads_each_model_once_and_composes_the_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asr_model = Mock()
    alignment_model = Mock()
    alignment_metadata = {"language": "fr"}
    diarization_pipeline = Mock()
    transcriber = Mock()
    storage = Mock()

    whisperx = SimpleNamespace(
        load_model=Mock(return_value=asr_model),
        load_align_model=Mock(return_value=(alignment_model, alignment_metadata)),
        load_audio=Mock(),
        align=Mock(),
        assign_word_speakers=Mock(),
    )
    whisperx_diarize = SimpleNamespace(
        DiarizationPipeline=Mock(return_value=diarization_pipeline)
    )
    module_loader = Mock(side_effect=[whisperx, whisperx_diarize])
    transcriber_factory = Mock(return_value=transcriber)
    monkeypatch.setattr(factory_module, "import_module", module_loader)
    monkeypatch.setattr(
        adapter_module,
        "WhisperXDiarizationTranscriber",
        transcriber_factory,
    )
    settings = WorkerLongFormDiarizationSettings(
        worker_id="long-form-diarization-1",
        worker_transcriber_hugging_face_token="hf_private",
        worker_transcriber_model="large-v3",
        worker_transcriber_device="cpu",
        worker_transcriber_compute_type="int8",
        worker_transcriber_batch_size=8,
        worker_transcriber_model_directory=tmp_path,
    )

    result = factory_module.create_whisperx_transcriber(
        settings=settings,
        storage=storage,
    )

    assert result is transcriber
    assert module_loader.call_args_list == [
        (("whisperx",),),
        (("whisperx.diarize",),),
    ]
    whisperx.load_model.assert_called_once_with(
        "large-v3",
        "cpu",
        compute_type="int8",
        language="fr",
        download_root=str(tmp_path / "whisperx"),
    )
    whisperx.load_align_model.assert_called_once_with(
        language_code="fr",
        device="cpu",
        model_dir=str(tmp_path / "alignment"),
    )
    whisperx_diarize.DiarizationPipeline.assert_called_once_with(
        model_name="pyannote/speaker-diarization-community-1",
        token="hf_private",
        device="cpu",
        cache_dir=str(tmp_path / "pyannote"),
    )
    transcriber_factory.assert_called_once_with(
        storage=storage,
        load_audio=whisperx.load_audio,
        asr_model=asr_model,
        align=whisperx.align,
        alignment_model=alignment_model,
        alignment_metadata=alignment_metadata,
        diarization_pipeline=diarization_pipeline,
        assign_word_speakers=whisperx.assign_word_speakers,
        device="cpu",
        batch_size=8,
    )
    assert (tmp_path / "whisperx").is_dir()
    assert (tmp_path / "alignment").is_dir()
    assert (tmp_path / "pyannote").is_dir()


def test_factory_explains_how_to_install_missing_optional_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_loader = Mock(side_effect=ModuleNotFoundError("whisperx"))
    monkeypatch.setattr(factory_module, "import_module", module_loader)
    settings = WorkerLongFormDiarizationSettings(
        worker_id="long-form-diarization-1",
        worker_transcriber_hugging_face_token="hf_never_log_this",
        worker_transcriber_model_directory=tmp_path,
    )

    with pytest.raises(RuntimeError, match="extra 'cpu' ou 'gpu'") as error:
        factory_module.create_whisperx_transcriber(
            settings=settings,
            storage=Mock(),
        )

    assert "hf_never_log_this" not in str(error.value)
    assert isinstance(error.value.__cause__, ModuleNotFoundError)
