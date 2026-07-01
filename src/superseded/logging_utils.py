from __future__ import annotations

import json
import logging
import sys

_RESERVED_LOG_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "getMessage",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "event": record.getMessage(),
            "level": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(fmt: str = "text", level: str = "WARNING") -> None:
    """Configure the root logger with a single stderr handler.

    Idempotent: clears existing handlers first so repeated calls never
    accumulate. ``fmt`` selects a human ("text") or JSON formatter; ``level``
    gates which records surface (applied to the root logger so third-party
    libraries respect it too).
    """
    numeric = getattr(logging, level.upper(), logging.WARNING)
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(numeric)
