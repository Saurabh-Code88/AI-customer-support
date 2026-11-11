"""JSON logging helpers with ticket context on every application log record."""
import json
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from .config import LOG_LEVEL

_ticket_id = ContextVar("support_agent_ticket_id", default="system")
_EMAIL = re.compile(r"\b([A-Z0-9._%+-])[A-Z0-9._%+-]*@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)


def redact_emails(value):
    """Mask email addresses recursively before data reaches logs or audit output."""
    if isinstance(value, str):
        return _EMAIL.sub(r"\1***@\2", value)
    if isinstance(value, list):
        return [redact_emails(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_emails(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_emails(item) for key, item in value.items()}
    return value


@contextmanager
def ticket_context(ticket_id: str):
    token = _ticket_id.set(ticket_id or "system")
    try:
        yield
    finally:
        _ticket_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "ticket_id": getattr(record, "ticket_id", None) or _ticket_id.get() or "system",
        }
        for field in ("graph_step", "approval_decision", "provider", "attempt", "error_type"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact_emails(entry), ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("support_agent")
    if not any(getattr(handler, "_support_agent_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._support_agent_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    return logger


JSON_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "support_agent.logging_config.JsonFormatter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        }
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "uvicorn": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "uvicorn.error": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "uvicorn.access": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}


