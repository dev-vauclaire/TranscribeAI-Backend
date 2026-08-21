import logging

import pytest

import dispatcher.main as main_module
from dispatcher.models import DispatchBatchResult


pytestmark = pytest.mark.unit


def make_result(
    *,
    stale_count: int = 0,
    error_count: int = 0,
) -> DispatchBatchResult:
    return DispatchBatchResult(
        selected_count=1,
        published_count=1,
        confirmed_count=0 if stale_count else 1,
        stale_count=stale_count,
        error_count=error_count,
    )


def test_main_logs_summary_and_returns_zero_when_batch_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def successful_dispatch() -> DispatchBatchResult:
        return make_result()

    monkeypatch.setattr(main_module, "_run_from_environment", successful_dispatch)

    with caplog.at_level(logging.INFO):
        exit_code = main_module.main()

    assert exit_code == 0
    assert "dispatch_batch_summary" in caplog.text
    assert "selected=1" in caplog.text
    assert "confirmed=1" in caplog.text


def test_main_returns_zero_for_a_stale_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stale_dispatch() -> DispatchBatchResult:
        return make_result(stale_count=1)

    monkeypatch.setattr(main_module, "_run_from_environment", stale_dispatch)

    assert main_module.main() == 0


def test_main_returns_one_when_an_individual_job_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def partially_failed_dispatch() -> DispatchBatchResult:
        return make_result(error_count=1)

    monkeypatch.setattr(
        main_module,
        "_run_from_environment",
        partially_failed_dispatch,
    )

    assert main_module.main() == 1


def test_main_returns_one_without_logging_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_dispatch() -> DispatchBatchResult:
        raise RuntimeError("REDIS_URL=redis://secret")

    monkeypatch.setattr(main_module, "_run_from_environment", failing_dispatch)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    assert "error_type=RuntimeError" in caplog.text
    assert "redis://secret" not in caplog.text


def test_main_returns_130_when_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def interrupted_dispatch() -> DispatchBatchResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_run_from_environment", interrupted_dispatch)

    assert main_module.main() == 130
