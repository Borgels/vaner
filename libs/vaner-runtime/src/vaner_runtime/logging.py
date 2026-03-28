"""Structured logging for the vaner runtime.

Provides JSON-formatted log output for production and human-readable
output for development. Supports correlation IDs that thread through
events -> jobs -> artifacts -> broker decisions.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar

# Thread-local correlation ID (propagated via contextvars)
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the current correlation ID (empty string if not set)."""
    return _correlation_id.get()


def set_correlation_id(cid: str | None = None) -> str:
    """Set correlation ID. Generates a UUID4 if cid is None. Returns the ID."""
    value = cid if cid is not None else str(uuid.uuid4())
    _correlation_id.set(value)
    return value


class VanerJsonFormatter(logging.Formatter):
    """JSON formatter for production log output."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": time.time(),
            "level": record.levelname,
            "component": record.name,
            "msg": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        # Include extra fields if present
        for key in ("job_id", "context_key", "artifact_key", "workflow_type"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(dev_mode: bool = True, log_file: str | None = None) -> None:
    """Configure the root logger.

    dev_mode=True uses a human-readable format with timestamps.
    dev_mode=False uses JSON (VanerJsonFormatter) for structured log ingestion.
    If log_file is provided, logs are also written to that file path.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove existing handlers to avoid duplication on repeated calls
    root.handlers.clear()

    if dev_mode:
        formatter = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = VanerJsonFormatter()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Optional file handler
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
