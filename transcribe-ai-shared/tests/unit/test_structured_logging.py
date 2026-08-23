import json
import logging
from io import StringIO
import sys
from unittest.mock import Mock
from uuid import UUID

import pytest

from transcribe_ai_shared.observability import (
    StructuredJsonFormatter,
    configure_logging,
    log_event,
)


pytestmark = pytest.mark.unit

JOB_UUID = UUID("8ac9afe8-1360-4b91-8710-58f78fb9a398")


def test_log_event_adds_the_available_correlation_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.structured.correlation")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(
            logger,
            logging.INFO,
            service="worker-fast",
            event="job_claimed",
            job_uuid=JOB_UUID,
            attempt_count=2,
            worker_id="fast-1",
            redis_message_id="1740000000000-0",
        )

    record = caplog.records[-1]
    assert record.service == "worker-fast"
    assert record.event == "job_claimed"
    assert record.job_uuid == str(JOB_UUID)
    assert record.attempt_count == 2
    assert record.worker_id == "fast-1"
    assert record.redis_message_id == "1740000000000-0"


def test_structured_formatter_emits_json_without_sensitive_arguments() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter(default_service="api"))
    logger = logging.getLogger("test.structured.privacy")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    try:
        log_event(
            logger,
            logging.INFO,
            service="api",
            event="job_created",
            job_uuid=JOB_UUID,
            attempt_count=0,
            status="QUEUED",
            audio=b"private audio bytes",
            transcription="private transcription",
            result={"text": "private result"},
            token="hf_secret_token",
        )
    finally:
        logger.handlers = []
        logger.propagate = True

    raw_log = stream.getvalue()
    payload = json.loads(raw_log)
    assert payload["service"] == "api"
    assert payload["event"] == "job_created"
    assert payload["job_uuid"] == str(JOB_UUID)
    assert payload["status"] == "QUEUED"
    assert "private" not in raw_log
    assert "hf_secret_token" not in raw_log
    assert "audio" not in payload
    assert "transcription" not in payload
    assert "result" not in payload
    assert "token" not in payload


def test_formatter_does_not_render_an_exception_message() -> None:
    formatter = StructuredJsonFormatter(default_service="dispatcher")

    try:
        raise RuntimeError("postgresql://user:password@database/transcriptions")
    except RuntimeError:
        record = logging.LogRecord(
            name="test.structured.exception",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="connection failed with a sensitive URL",
            args=(),
            exc_info=sys.exc_info(),
        )

    raw_log = formatter.format(record)
    payload = json.loads(raw_log)
    assert payload["event"] == "unstructured_log"
    assert payload["error_type"] == "RuntimeError"
    assert "password" not in raw_log
    assert "sensitive URL" not in raw_log


def test_configure_logging_replaces_an_existing_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    basic_config = Mock()
    monkeypatch.setattr(logging, "basicConfig", basic_config)

    configure_logging("dispatcher")

    basic_config.assert_called_once()
    options = basic_config.call_args.kwargs
    assert options["level"] == logging.INFO
    assert options["force"] is True
    handler = options["handlers"][0]
    assert isinstance(handler.formatter, StructuredJsonFormatter)


def test_uvicorn_access_log_keeps_only_safe_http_fields() -> None:
    formatter = StructuredJsonFormatter(default_service="api")
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "192.0.2.10:1234",
            "POST",
            "/api/transcriptions?token=secret",
            "1.1",
            202,
        ),
        exc_info=None,
    )

    raw_log = formatter.format(record)
    payload = json.loads(raw_log)
    assert payload["event"] == "http_request_completed"
    assert payload["http_method"] == "POST"
    assert payload["http_status_code"] == 202
    assert "192.0.2.10" not in raw_log
    assert "/api/transcriptions" not in raw_log
    assert "secret" not in raw_log
