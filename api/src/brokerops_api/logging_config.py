"""JSON log discipline for Cloud Run (BOP-035).

Cloud Logging scrapes stdout/stderr as text lines; when each line is one JSON
object, log-based metrics and alerting can filter on fields without a code
change. Enable with ``LOG_FORMAT=json`` (Terraform sets this on Cloud Run) or
auto when ``K_SERVICE`` is present (Cloud Run always sets it).

Fields on every record:
  severity   — Cloud Logging severity (DEBUG/INFO/WARNING/ERROR/CRITICAL)
  message    — the log message
  logger     — Python logger name (e.g. uvicorn.access, brokerops_api.…)
  time       — ISO-8601 UTC timestamp
  service    — fixed "brokerops-api" (stable filter key across instances)
  version    — IMAGE_VERSION when set (ties a log line to a release pin)

Access logs (uvicorn.access) additionally carry:
  method, path, status_code, client  — when the record has those attrs
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any


_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line — Cloud Logging's preferred structured form."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "service": "brokerops-api",
        }
        version = os.environ.get("IMAGE_VERSION") or os.environ.get("BROKEROPS_VERSION")
        if version:
            payload["version"] = version
        # uvicorn.access puts the request line in the message; also surface
        # structured attrs when a custom AccessFormatter/filter sets them.
        for key in ("method", "path", "status_code", "client"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def json_logging_enabled() -> bool:
    explicit = os.environ.get("LOG_FORMAT", "").strip().lower()
    if explicit in {"json", "structured"}:
        return True
    if explicit in {"text", "plain"}:
        return False
    # Cloud Run sets K_SERVICE on every revision — default to JSON there so a
    # deploy gets log-based alerting without an extra env var, while local
    # compose/dev stays human-readable text.
    return bool(os.environ.get("K_SERVICE"))


def configure_logging() -> None:
    """Install the JSON formatter on the root + uvicorn loggers when enabled.

    Idempotent and safe to call at import: only mutates handlers' formatters
    (and adds a StreamHandler if the root has none). Does not reconfigure when
    text mode is active, so pytest/local output stays unchanged.
    """
    if not json_logging_enabled():
        return
    formatter = JsonLogFormatter()
    root = logging.getLogger()
    if not root.handlers:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
        root.setLevel(logging.INFO)
    else:
        for existing in root.handlers:
            existing.setFormatter(formatter)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.propagate = True
        for existing in logger.handlers:
            existing.setFormatter(formatter)
