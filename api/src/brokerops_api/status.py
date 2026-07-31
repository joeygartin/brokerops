"""Instance health detail for /statusz (BOP-035).

Builds the compact JSON a fleet operator (or deploy-side alerter) reads:
version, orchestrator, selector names only, alembic head-vs-current, last cron
outcome, uptime. No secret-shaped values — selectors are closed-enum names, never
keys/URLs/tokens.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from brokerops_api.db import CronRunRecord, CronRunStore

# Cron is "stale" when it has never run or finished more than this many seconds ago.
CRON_STALE_AFTER_SECONDS = 24 * 60 * 60
MILESTONE_CRON_JOB = "milestones"

# Selectors whose *values* are provider/backend names (never credentials). Keep
# this list the single place the status surface names backends.
_SELECTOR_ENVS: tuple[tuple[str, str, str], ...] = (
    # (payload_key, env_var, default_when_unset)
    ("crm", "CRM_VENDOR", "followupboss"),
    ("email", "EMAIL_PROVIDER", "stub"),
    ("sms", "SMS_PROVIDER", "stub"),
    ("files", "FILES_PROVIDER", "stub"),
    ("extraction", "EXTRACTION_BACKEND", "deterministic"),
    ("drafting", "DRAFTING_BACKEND", "deterministic"),
)


def instance_version() -> str:
    """Release pin from BOP-031's image tag when Terraform injects it; else dev."""
    return os.environ.get("IMAGE_VERSION") or os.environ.get("BROKEROPS_VERSION") or "dev"


def safe_error_label(exc: BaseException) -> str:
    """Persist only the exception type for /statusz — never ``str(exc)``.

    Raw exception text can embed DSNs, API keys, or request bodies; the status
    surface must stay free of secret-shaped values (BOP-035 acceptance).
    """
    return type(exc).__name__


def orchestrator_name() -> str:
    return os.environ.get("ORCHESTRATOR", "langgraph").strip().lower() or "langgraph"


def selector_summary() -> dict[str, str]:
    """Provider/backend names only — the values of the closed selectors, never
    companion secrets (API keys, DSNs, webhook secrets, base URLs)."""
    out: dict[str, str] = {}
    for key, env, default in _SELECTOR_ENVS:
        raw = os.environ.get(env, "").strip().lower()
        out[key] = raw or default
    return out


def _alembic_ini_path() -> Path:
    # api/src/brokerops_api/status.py → api/alembic.ini
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_head_revision() -> str | None:
    """Filesystem head revision (what this image's migrations tip at)."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(_alembic_ini_path()))
        script = ScriptDirectory.from_config(cfg)
        return script.get_current_head()
    except Exception:  # noqa: BLE001 — status surface never 500s on probe failure
        return None


async def alembic_current_revision(engine: AsyncEngine | None) -> str | None:
    """DB's alembic_version row, or None when there is no database / no table yet.

    Uses the engine the caller supplies. Prefer MIGRATION_DATABASE_URL's engine
    when roles are split — the least-privilege runtime role deliberately cannot
    SELECT alembic_version (migration 0010).
    """
    if engine is None:
        return None
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            row = result.first()
        return str(row[0]) if row is not None else None
    except Exception:  # noqa: BLE001 — missing table / permission / down DB
        return None


async def migration_status(engine: AsyncEngine | None) -> dict[str, Any]:
    head = alembic_head_revision()
    if engine is None:
        # Database-less demo/tests: no schema to drift; report matched.
        return {
            "current": None,
            "head": head,
            "matched": True,
            "mode": "no-database",
        }
    current = await alembic_current_revision(engine)
    matched = current is not None and head is not None and current == head
    return {
        "current": current,
        "head": head,
        "matched": matched,
        "mode": "database",
    }


def cron_payload(
    record: CronRunRecord | None,
    *,
    now: datetime | None = None,
    stale_after: int = CRON_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Shape the last-cron block; ``stale`` is true when never-run or >24h old."""
    clock = now or datetime.now(UTC)
    if record is None:
        return {
            "job": MILESTONE_CRON_JOB,
            "outcome": None,
            "finished_at": None,
            "checked": None,
            "skipped_pending_escalation": None,
            "email_tail_suppressed": None,
            "error": None,
            "age_seconds": None,
            "stale": True,
        }
    finished = record.finished_at
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    age = max(0, int((clock - finished).total_seconds()))
    body = record.as_dict()
    body["age_seconds"] = age
    body["stale"] = age > stale_after or record.outcome == "failure"
    return body


async def build_status_payload(
    *,
    started_at: float,
    cron_store: CronRunStore,
    migration_engine: AsyncEngine | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    migrations = await migration_status(migration_engine)
    last = await cron_store.latest(MILESTONE_CRON_JOB)
    last_cron = cron_payload(last, now=now)
    degraded = (not migrations["matched"]) or bool(last_cron["stale"])
    return {
        "status": "degraded" if degraded else "ok",
        "version": instance_version(),
        "orchestrator": orchestrator_name(),
        "selectors": selector_summary(),
        "migrations": migrations,
        "last_cron": last_cron,
        "uptime_seconds": round(max(0.0, time.monotonic() - started_at), 3),
    }
