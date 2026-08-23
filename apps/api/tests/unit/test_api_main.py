import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import api.main as api_main


pytestmark = pytest.mark.unit


def test_main_configures_json_logging_without_uvicorn_overriding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(host="127.0.0.1", port=8000)
    app = object()
    configure_logging = MagicMock()
    create_app = MagicMock(return_value=app)
    run_uvicorn = MagicMock()
    monkeypatch.setattr(api_main, "configure_logging", configure_logging)
    monkeypatch.setattr(api_main, "ApiSettings", MagicMock(return_value=settings))
    monkeypatch.setattr(api_main, "create_app", create_app)
    monkeypatch.setattr(api_main.uvicorn, "run", run_uvicorn)

    exit_code = api_main.main()

    assert exit_code == 0
    configure_logging.assert_called_once_with(service=api_main.SERVICE)
    create_app.assert_called_once_with(settings=settings)
    run_uvicorn.assert_called_once_with(
        app,
        host="127.0.0.1",
        log_config=None,
        port=8000,
    )


def test_main_returns_one_without_logging_startup_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup_error = RuntimeError("DATABASE_URL=postgresql://user:secret@database")
    record_event = MagicMock()
    monkeypatch.setattr(api_main, "configure_logging", MagicMock())
    monkeypatch.setattr(api_main, "ApiSettings", MagicMock(side_effect=startup_error))
    monkeypatch.setattr(api_main, "log_event", record_event)

    exit_code = api_main.main()

    assert exit_code == 1
    record_event.assert_called_once_with(
        api_main.LOGGER,
        logging.ERROR,
        service=api_main.SERVICE,
        event="api_process",
        action="failed",
        error_type="RuntimeError",
    )
    assert "secret" not in repr(record_event.call_args)


def test_main_returns_130_when_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(host="127.0.0.1", port=8000)
    record_event = MagicMock()
    monkeypatch.setattr(api_main, "configure_logging", MagicMock())
    monkeypatch.setattr(api_main, "ApiSettings", MagicMock(return_value=settings))
    monkeypatch.setattr(api_main, "create_app", MagicMock(return_value=object()))
    monkeypatch.setattr(
        api_main.uvicorn,
        "run",
        MagicMock(side_effect=KeyboardInterrupt),
    )
    monkeypatch.setattr(api_main, "log_event", record_event)

    exit_code = api_main.main()

    assert exit_code == 130
    record_event.assert_called_once_with(
        api_main.LOGGER,
        logging.WARNING,
        service=api_main.SERVICE,
        event="api_process",
        action="interrupted",
    )
