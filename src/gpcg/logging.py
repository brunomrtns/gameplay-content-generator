"""Structured logging with correlatable job IDs."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from rich.logging import RichHandler

_FORMAT = "%(message)s"
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger with rich handler. Idempotent."""
    global _configured
    if _configured:
        return
    handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        markup=True,
        log_time_format="[%X]",
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # Uvicorn / other libs can be noisy
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str, job_id: Optional[str] = None) -> logging.Logger:
    """Get a logger. If job_id given, it's embedded in every message via extra."""
    logger = logging.getLogger(name)
    if job_id is not None:
        logger = logging.LoggerAdapter(logger, {"job_id": job_id})
    return logger


class JobLogAdapter(logging.LoggerAdapter):
    """Prepends [job=xxxx] to log messages."""

    def process(self, msg, kwargs):
        jid = self.extra.get("job_id", "?") if self.extra else "?"
        return f"[job={jid}] {msg}", kwargs
