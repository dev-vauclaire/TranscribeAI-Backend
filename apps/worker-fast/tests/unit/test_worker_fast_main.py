import logging

import pytest

import worker_fast.main as main_module
from transcribe_ai_shared.worker.testing import FakeTranscriber
from worker_fast.config import WorkerFastSettings


pytestmark = pytest.mark.unit


def test_create_transcriber_requires_an_explicit_backend() -> None:
    with pytest.raises(RuntimeError, match="Aucun backend"):
        main_module._create_transcriber(WorkerFastSettings(worker_id="fast-1"))


def test_create_transcriber_allows_fake_only_with_development_opt_in() -> None:
    transcriber = main_module._create_transcriber(
        WorkerFastSettings(
            worker_id="fast-1",
            worker_environment="development",
            worker_transcriber_backend="fake",
        )
    )

    assert isinstance(transcriber, FakeTranscriber)


def test_main_returns_one_if_the_long_running_worker_stops_unexpectedly(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def stopped_process() -> None:
        return None

    monkeypatch.setattr(main_module, "_run_from_environment", stopped_process)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    assert "worker_fast_stopped_unexpectedly" in caplog.text


def test_main_returns_one_without_logging_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_process() -> object:
        raise RuntimeError("REDIS_URL=redis://secret")

    monkeypatch.setattr(main_module, "_run_from_environment", failing_process)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    assert "error_type=RuntimeError" in caplog.text
    assert "redis://secret" not in caplog.text


def test_main_returns_130_when_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted_process() -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_run_from_environment", interrupted_process)

    assert main_module.main() == 130
