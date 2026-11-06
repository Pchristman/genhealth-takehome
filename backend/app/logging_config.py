"""JSON logs built from an allowlist, so a stray extra= cannot put patient data in the logs.

Exception messages are dropped too: they often quote the value that failed to parse.
"""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

ALLOWED_EXTRA = {"method", "route", "status_code", "duration_ms", "model", "attempt"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # The template only. Formatted args are where values would leak.
            "event": record.msg if isinstance(record.msg, str) else "non_string_log_message",
            "request_id": request_id_var.get(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k in ALLOWED_EXTRA})
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
            # Frames only: formatted tracebacks carry messages and source lines.
            frames = traceback.extract_tb(record.exc_info[2])
            entry["stack"] = [f"{f.filename}:{f.lineno} {f.name}" for f in frames]
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Uvicorn installs its own handlers, whose tracebacks would bypass the formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our request log
    for name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
