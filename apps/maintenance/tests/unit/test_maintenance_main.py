import logging

import pytest

import maintenance.main as main_module
from maintenance.storage_cleanup import (
    StorageCleanupAbortedError,
    StorageCleanupResult,
)


pytestmark = pytest.mark.unit


def cleanup_result(*, error_count: int = 0) -> StorageCleanupResult:
    return StorageCleanupResult(
        inspected_count=3,
        kept_count=2,
        deleted_count=1,
        orphan_deleted_count=1,
        too_recent_count=1,
        error_count=error_count,
    )


def test_main_logs_summary_and_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def successful_cleanup() -> StorageCleanupResult:
        return cleanup_result()

    monkeypatch.setattr(main_module, "_run_from_environment", successful_cleanup)

    with caplog.at_level(logging.INFO):
        exit_code = main_module.main()

    assert exit_code == 0
    assert "storage_cleanup_summary" in caplog.text
    assert "inspected=3" in caplog.text
    assert "deleted=1" in caplog.text


def test_main_returns_one_when_some_directories_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def partial_cleanup() -> StorageCleanupResult:
        return cleanup_result(error_count=1)

    monkeypatch.setattr(main_module, "_run_from_environment", partial_cleanup)

    assert main_module.main() == 1


def test_main_returns_one_when_cleanup_is_aborted(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def aborted_cleanup() -> StorageCleanupResult:
        raise StorageCleanupAbortedError("DATABASE_URL=secret")

    monkeypatch.setattr(main_module, "_run_from_environment", aborted_cleanup)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    assert "storage_cleanup_aborted" in caplog.text
    assert "DATABASE_URL=secret" not in caplog.text


def test_main_returns_one_without_logging_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_cleanup() -> StorageCleanupResult:
        raise RuntimeError("DATABASE_URL=secret")

    monkeypatch.setattr(main_module, "_run_from_environment", failing_cleanup)

    with caplog.at_level(logging.ERROR):
        exit_code = main_module.main()

    assert exit_code == 1
    assert "error_type=RuntimeError" in caplog.text
    assert "DATABASE_URL=secret" not in caplog.text


def test_main_returns_130_when_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def interrupted_cleanup() -> StorageCleanupResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_run_from_environment", interrupted_cleanup)

    assert main_module.main() == 130
