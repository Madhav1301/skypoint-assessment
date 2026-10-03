"""Structured JSON logging.

PHI safety: callers must only pass identifiers (batch_id, file_name, row
numbers, counts, reason codes) as context — never field values from the data.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_LOGGER_NAME = "pipeline"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if ctx:
            payload.update(ctx)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def _log(level: int, msg: str, **ctx) -> None:
    logging.getLogger(_LOGGER_NAME).log(level, msg, extra={"ctx": ctx})


def debug(msg: str, **ctx) -> None:
    _log(logging.DEBUG, msg, **ctx)


def info(msg: str, **ctx) -> None:
    _log(logging.INFO, msg, **ctx)


def warning(msg: str, **ctx) -> None:
    _log(logging.WARNING, msg, **ctx)


def error(msg: str, **ctx) -> None:
    _log(logging.ERROR, msg, **ctx)
