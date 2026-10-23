"""Structured (JSON-lines) logging for TCL."""
from __future__ import annotations

import json
import logging as _logging
import sys
import time
from typing import Any

_RESERVED = set(vars(_logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message"}


class JsonFormatter(_logging.Formatter):
    def format(self, record: _logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(time.time(), 6),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED:
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def get_logger(name: str) -> _logging.Logger:
    return _logging.getLogger(f"tcl.{name}")


def configure(level: str = "INFO", stream=None) -> None:
    root = _logging.getLogger("tcl")
    for h in list(root.handlers):
        root.removeHandler(h)
    h = _logging.StreamHandler(stream or sys.stderr)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level.upper())
    root.propagate = False
