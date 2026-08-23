import logging

import pytest

import maintenance.main as main_module
from maintenance.storage_cleanup import (
    StorageCleanupAbortedError,
    StorageCleanupResult,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def structured_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], list[dict[str, object]]]:
    configured_services: list[str] = []
    events: list[dict[str, object]] = []

    def record_configuration(*, service: str) -> None:
        configured_services.append(service)

    def record_event(
        logger: logging.Logger,
        level: int,
        *,
        service: str,
        event: str,
        **fields: object,
    ) -> None:
        events.append(
            {
                "logger": logger,
                "level": level,
                "service": service,
                "event": event,
                **fields,
            }
        )

    monkeypatch.setattr(main_module, "configure_logging", record_configuration)
    monkeypatch.setattr(main_module, "log_event", record_event)
    return configured_services, events


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
    structured_logging: tuple[list[str], list[dict[str, object]]],
) -> None:
    async def successful_cleanup() -> StorageCleanupResult:
        return cleanup_result()

    monkeypatch.setattr(main_module, "_run_from_environment", successful_cleanup)

    exit_code = main_module.main()

    assert exit_code == 0
    configured_services, events = structured_logging
    assert configured_services == ["maintenance"]
    assert events == [
        {
            "logger": main_module.LOGGER,
            "level": logging.INFO,
            "service": "maintenance",
            "event": "cleanup",
            "action": "summary",
            "inspected_count": 3,
            "kept_count": 2,
            "deleted_count": 1,
            "orphan_deleted_count": 1,
            "too_recent_count": 1,
            "error_count": 0,
        }
    ]


def test_main_returns_one_when_some_directories_failed(
    monkeypatch: pytest.MonkeyPatch,
    structured_logging: tuple[list[str], list[dict[str, object]]],
) -> None:
    async def partial_cleanup() -> StorageCleanupResult:
        return cleanup_result(error_count=1)

    monkeypatch.setattr(main_module, "_run_from_environment", partial_cleanup)

    assert main_module.main() == 1
    assert structured_logging[1][0]["error_count"] == 1


def test_main_returns_one_when_cleanup_is_aborted(
    monkeypatch: pytest.MonkeyPatch,
    structured_logging: tuple[list[str], list[dict[str, object]]],
) -> None:
    async def aborted_cleanup() -> StorageCleanupResult:
        error = RuntimeError("DATABASE_URL=secret")
        raise StorageCleanupAbortedError(
            dependency="postgresql",
            reason="job_lookup_failed",
            error_type=type(error).__name__,
        ) from error

    monkeypatch.setattr(main_module, "_run_from_environment", aborted_cleanup)

    exit_code = main_module.main()

    assert exit_code == 1
    event = structured_logging[1][0]
    assert event["level"] == logging.ERROR
    assert event["event"] == "cleanup"
    assert event["action"] == "abort"
    assert event["dependency"] == "postgresql"
    assert event["reason"] == "job_lookup_failed"
    assert event["error_type"] == "RuntimeError"
    assert "DATABASE_URL=secret" not in repr(event)


def test_main_returns_one_without_logging_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    structured_logging: tuple[list[str], list[dict[str, object]]],
) -> None:
    async def failing_cleanup() -> StorageCleanupResult:
        raise RuntimeError("DATABASE_URL=secret")

    monkeypatch.setattr(main_module, "_run_from_environment", failing_cleanup)

    exit_code = main_module.main()

    assert exit_code == 1
    event = structured_logging[1][0]
    assert event["level"] == logging.ERROR
    assert event["event"] == "cleanup"
    assert event["action"] == "failed"
    assert event["reason"] == "unexpected_error"
    assert event["error_type"] == "RuntimeError"
    assert "DATABASE_URL=secret" not in repr(event)


def test_main_returns_130_when_interrupted(
    monkeypatch: pytest.MonkeyPatch,
    structured_logging: tuple[list[str], list[dict[str, object]]],
) -> None:
    async def interrupted_cleanup() -> StorageCleanupResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_run_from_environment", interrupted_cleanup)

    assert main_module.main() == 130
    event = structured_logging[1][0]
    assert event["level"] == logging.WARNING
    assert event["event"] == "cleanup"
    assert event["action"] == "interrupted"
    assert event["reason"] == "keyboard_interrupt"
