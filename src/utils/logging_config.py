"""Application logging configuration.

The request/response middleware attaches structured fields to log records
via ``logging``'s ``extra={}`` mechanism (path, status code, latency,
authenticated identity). The stdlib's default formatter renders only the
fields named in its format string, so every one of those extras is
silently dropped.

This module provides a JSON formatter that emits the extras, plus a
``configure_logging`` helper. JSON is used in production (so CloudWatch
Logs Insights, Loki, etc. can query the fields); plain text is kept for
local development where logs are read by eye.
"""

from __future__ import annotations

import datetime
import json
import logging

# LogRecord attributes that the stdlib sets itself. Anything on a record
# that is *not* in this set was supplied by the caller via extra={} and is
# what we want to surface.
_STANDARD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
})


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON, including ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Caller-supplied extras (the whole point of this formatter).
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        # default=str so non-serialisable values (UUIDs, datetimes) degrade
        # gracefully instead of crashing the log call.
        return json.dumps(payload, default=str)


def configure_logging(level: str, *, json_format: bool) -> None:
    """Install a single root handler with the chosen formatter.

    Args:
        level: Root log level (``DEBUG``, ``INFO``, ...).
        json_format: ``True`` for JSON output (production), ``False`` for
            the human-readable text format (local development).
    """
    handler = logging.StreamHandler()
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
