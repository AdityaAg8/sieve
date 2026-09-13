"""Structured JSON logs with `scan_id`, and never a raw tool description above DEBUG (SC-5).

The filter is belt and braces: call sites already log identifiers rather than content, but a
future call site that forgets would leak tool text into a log aggregator, and that is exactly
the data Sieve promises not to keep.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from sieve.config import settings

# Reserved attributes of `logging.LogRecord`; everything else on a record came from `extra`.
_RESERVED = frozenset(
    (
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    )
)

# Keys whose value is tool-definition content rather than an identifier.
CONTENT_KEYS = frozenset({"description", "tool_text", "serialised", "prompt", "payload_text"})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class DropToolContent(logging.Filter):
    """Strip content-bearing `extra` keys from anything at INFO or above (SC-5)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.DEBUG:
            for key in CONTENT_KEYS & record.__dict__.keys():
                record.__dict__[key] = "[dropped: above DEBUG]"
        return True


def configure_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(DropToolContent())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel((level or settings.log_level).upper())
