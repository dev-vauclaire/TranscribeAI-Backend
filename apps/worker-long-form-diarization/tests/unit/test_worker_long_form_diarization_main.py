import logging
from unittest.mock import Mock

import pytest

import worker_long_form_diarization.main as main_module
import worker_long_form_diarization.transcribers.whisperx_factory as factory_module
from transcribe_ai_shared.worker.testing import FakeTranscriber
from worker_long_form_diarization.config import WorkerLongFormDiarizationSettings


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def preserve_test_logging_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> Mock:
    configure_logging = Mock()
    monkeypatch.setattr(main_module, "configure_logging", configure_logging)
    return configure_logging


def test_create_transcriber_delegates_whisperx_composition_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcriber = Mock()
    factory = Mock(return_value=transcriber)
    storage = Mock()
    settings = WorkerLongFormDiarizationSettings(
        worker_id="long-form-diarization-1",
        worker_transcriber_hugging_face_token="hf_test",
    )
    monkeypatch.setattr(factory_module, "create_whisperx_transcriber", factory)

    result = main_module._create_transcriber(settings, storage)

    assert result is transcriber
    factory.assert_called_once_with(settings=settings, storage=storage)


def test_create_transcriber_allows_fake_only_with_development_opt_in() -> None:
    transcriber = main_module._create_transcriber(
        WorkerLongFormDiarizationSettings(
            worker_id="long-form-diarization-1",
            worker_environment="development",
            worker_transcriber_backend="fake",
        ),
        Mock(),
    )

    assert isinstance(transcriber, FakeTranscriber)


def test_main_returns_one_if_the_long_running_worker_stops_unexpectedly(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    preserve_test_logging_configuration: Mock,
) -> None:
    async def stopped_process() -> None:
        return None

    monkeypatch.setattr(main_module, "_run_from_environment", stopped_process)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    preserve_test_logging_configuration.assert_called_once_with(
        service="worker-long-form-diarization"
    )
    assert "worker_stopped_unexpectedly" in caplog.text


def test_main_returns_one_without_logging_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_process() -> object:
        raise RuntimeError("DATABASE_URL=postgresql://secret")

    monkeypatch.setattr(main_module, "_run_from_environment", failing_process)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    record = caplog.records[-1]
    assert record.event == "worker_process_failed"  # type: ignore[attr-defined]
    assert record.service == (  # type: ignore[attr-defined]
        "worker-long-form-diarization"
    )
    assert record.error_type == "RuntimeError"  # type: ignore[attr-defined]
    assert "postgresql://secret" not in caplog.text


def test_main_returns_130_when_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted_process() -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_run_from_environment", interrupted_process)

    assert main_module.main() == 130
