"""Tests for JSON logging configuration."""

import json
import logging
import uuid

import pytest
from src.utils.logging_config import JsonFormatter, configure_logging

pytestmark = pytest.mark.unit


def _record(**extra) -> logging.LogRecord:
    """Build a LogRecord with optional extra fields, the way logging does."""
    record = logging.LogRecord(
        name="src.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_valid_json_with_core_fields():
    out = json.loads(JsonFormatter().format(_record()))
    assert out["level"] == "INFO"
    assert out["logger"] == "src.test"
    assert out["message"] == "hello world"  # %-args rendered
    assert "timestamp" in out


def test_includes_extra_fields():
    out = json.loads(JsonFormatter().format(_record(
        path="/api/v1/documents",
        status_code=200,
        user_sub="zach",
    )))
    assert out["path"] == "/api/v1/documents"
    assert out["status_code"] == 200
    assert out["user_sub"] == "zach"


def test_does_not_emit_standard_record_internals():
    out = json.loads(JsonFormatter().format(_record()))
    # Internal LogRecord attributes must not leak into the payload.
    for noise in ("args", "msg", "levelno", "pathname", "thread"):
        assert noise not in out


def test_non_serialisable_extra_degrades_gracefully():
    job_id = uuid.uuid4()
    out = json.loads(JsonFormatter().format(_record(job_id=job_id)))
    # default=str keeps the log call from crashing on a UUID.
    assert out["job_id"] == str(job_id)


def test_includes_exception_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = _record()
        record.exc_info = sys.exc_info()
        out = json.loads(JsonFormatter().format(record))
    assert "exc_info" in out
    assert "ValueError: boom" in out["exc_info"]


def test_configure_logging_json_installs_json_formatter():
    try:
        configure_logging(level="INFO", json_format=True)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.INFO
    finally:
        logging.getLogger().handlers.clear()


def test_configure_logging_text_installs_plain_formatter():
    try:
        configure_logging(level="DEBUG", json_format=False)
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG
    finally:
        logging.getLogger().handlers.clear()
