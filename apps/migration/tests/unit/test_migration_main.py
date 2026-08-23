from importlib import import_module
import logging
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit
main_module = import_module("migration.main")


@pytest.fixture
def entrypoint_events(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, MagicMock]:
    configure_logging = MagicMock()
    log_event = MagicMock()
    monkeypatch.setattr(main_module, "configure_logging", configure_logging)
    monkeypatch.setattr(main_module, "log_event", log_event)
    return configure_logging, log_event


def test_main_logs_completion_only_after_the_migration(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint_events: tuple[MagicMock, MagicMock],
) -> None:
    timeline: list[str] = []
    configure_logging, log_event = entrypoint_events

    def upgrade_database() -> None:
        timeline.append("upgrade")

    def record_event(*_args: object, **fields: object) -> None:
        timeline.append(f"log:{fields['action']}")

    monkeypatch.setattr(main_module, "upgrade_database", upgrade_database)
    log_event.side_effect = record_event

    exit_code = main_module.main()

    assert exit_code == 0
    configure_logging.assert_called_once_with(service="migration")
    assert timeline == ["log:started", "upgrade", "log:completed"]


def test_main_returns_one_without_logging_migration_error_details(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint_events: tuple[MagicMock, MagicMock],
) -> None:
    _, log_event = entrypoint_events

    def fail_upgrade() -> None:
        raise RuntimeError("DATABASE_URL=postgresql://user:secret@database")

    monkeypatch.setattr(main_module, "upgrade_database", fail_upgrade)

    exit_code = main_module.main()

    assert exit_code == 1
    failed_call = log_event.call_args_list[-1]
    assert failed_call.kwargs == {
        "service": "migration",
        "event": "migration",
        "action": "failed",
        "dependency": "postgresql",
        "error_type": "RuntimeError",
    }
    assert failed_call.args == (main_module.LOGGER, logging.ERROR)
    assert "secret" not in repr(log_event.call_args_list)
    assert all(
        call.kwargs["action"] != "completed" for call in log_event.call_args_list
    )


def test_main_returns_130_when_migration_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint_events: tuple[MagicMock, MagicMock],
) -> None:
    _, log_event = entrypoint_events
    monkeypatch.setattr(
        main_module,
        "upgrade_database",
        MagicMock(side_effect=KeyboardInterrupt),
    )

    exit_code = main_module.main()

    assert exit_code == 130
    interrupted_call = log_event.call_args_list[-1]
    assert interrupted_call.args == (main_module.LOGGER, logging.WARNING)
    assert interrupted_call.kwargs["action"] == "interrupted"
