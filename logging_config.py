"""Structured logging with environment-secret redaction."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone


class SecretRedactionFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self._secrets = tuple(
            value
            for name in ("OPENAI_API_KEY", "SUPERCELL_API_TOKEN", "ADMIN_API_KEY")
            if (value := os.getenv(name, ""))
        )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message[:8000]
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    if os.getenv("LOG_FORMAT", "text").strip().lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
