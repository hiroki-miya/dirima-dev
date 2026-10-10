"""Minimal structured logging without request bodies or credentials."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from dirima.security.redaction import redact

FIELDS = ("trace_id", "workspace_id", "project_id", "task_id", "run_id", "event")

UVICORN_LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "dirima.infrastructure.logging.JsonFormatter"}},
    "handlers": {
        "json": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        }
    },
    "loggers": {
        "uvicorn": {"handlers": ["json"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO", "propagate": True},
    },
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(
                record, "service", "api" if record.name.startswith("uvicorn") else "dirima"
            ),
            **{name: getattr(record, name, None) for name in FIELDS},
            "message": redact(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0]:
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(redact(data), ensure_ascii=False, default=str)


def configure(service: str) -> logging.Logger:
    logger = logging.getLogger(f"dirima.{service}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
