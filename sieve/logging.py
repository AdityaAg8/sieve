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
from sieve.errors import MAX_TRACEBACK_CHARS, safe_detail

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
            # A traceback is a writer too (fix round 2, I4). `DropToolContent` below only
            # rewrites the named content keys taken from `extra`; it never sees `exc`. And the
            # one caller that matters -- `AnthropicJudge.judge`'s `LOGGER.exception` -- fires
            # from inside the `try` that wraps `redact_tool`, i.e. the one place where the
            # record has *not yet been redacted*, so a `ValidationError` raised while redacting
            # printed the value it rejected straight into the aggregator at ERROR.
            # Bounded at `MAX_TRACEBACK_CHARS`, not at the row bound: a traceback's useful
            # part is its *last* line, so the 500-character column limit would cut the
            # exception type and message off the end of anything deeper than six frames.
            payload["exc"] = safe_detail(
                self.formatException(record.exc_info), limit=MAX_TRACEBACK_CHARS
            )
        return json.dumps(payload, default=str, ensure_ascii=False)


class DropToolContent(logging.Filter):
    """Strip content-bearing `extra` keys from anything at INFO or above (SC-5)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.DEBUG:
            for key in CONTENT_KEYS & record.__dict__.keys():
                record.__dict__[key] = "[dropped: above DEBUG]"
        return True


def _ensure_drop_filter(target: logging.Logger | logging.Handler) -> None:
    if not any(isinstance(f, DropToolContent) for f in target.filters):
        target.addFilter(DropToolContent())


def configure_logging(level: str | None = None, *, replace: bool = True) -> None:
    """Install the JSON handler, and the SC-5 filter *whatever* else is configured.

    `replace=False` leaves an already-configured root's handlers alone -- under pytest the
    root logger belongs to the test runner, and stealing its handlers would silence `caplog`
    for every other lane. What it must never leave alone is `DropToolContent`: SC-5 is a
    binding constraint, not a default, so the filter is attached to every root handler and to
    the `sieve` logger regardless of who configured logging first.
    """
    root_logger = logging.getLogger()
    if replace or not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root_logger.handlers = [handler]
        root_logger.setLevel((level or settings.log_level).upper())

    for existing in root_logger.handlers:
        _ensure_drop_filter(existing)
    # The root handlers are what actually enforce this for `sieve.*` children, since
    # `callHandlers` walks ancestors' *handlers* and not their filters; the logger-level
    # filter only covers records logged on `sieve` itself. Both are cheap, so do both.
    _ensure_drop_filter(logging.getLogger("sieve"))
