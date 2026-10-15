"""Structured logging on top of the standard library.

Records are emitted as one JSON object per line (or a readable console format for local work).
Request-scoped fields come from context variables, which are per-task in asyncio, so concurrent
requests never share them.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
tool_name_var: ContextVar[str | None] = ContextVar("tool_name", default=None)

# Attributes present on every LogRecord; anything else was passed via ``extra=``.
_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn duplicate of message with ANSI codes
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
        }
        if (cid := correlation_id_var.get()) is not None:
            payload["correlation_id"] = cid
        if (tool := tool_name_var.get()) is not None:
            payload["tool"] = tool
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s%(context)s")

    def format(self, record: logging.LogRecord) -> str:
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED and k != "context"}
        if (cid := correlation_id_var.get()) is not None:
            extras["correlation_id"] = cid
        if (tool := tool_name_var.get()) is not None:
            extras["tool"] = tool
        record.context = (" " + " ".join(f"{k}={v}" for k, v in extras.items())) if extras else ""
        return super().format(record)


def configure_logging(*, level: str, fmt: str, service_name: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name) if fmt == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # httpx logs full request URLs at INFO; with OAuth1 those carry the consumer key and
    # signature in the query string, so keep it (and httpcore) quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers[:] = []
        uv_logger.propagate = True
