"""BOP-035: /statusz detail, cron outcome recording, selector names-only."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brokerops_api.db import CronRunRecord, InMemoryCronRunStore
from brokerops_api.logging_config import JsonLogFormatter, json_logging_enabled
from brokerops_api.main import app
from brokerops_api.status import (
    CRON_STALE_AFTER_SECONDS,
    build_status_payload,
    cron_payload,
    selector_summary,
)
from brokerops_core.models.role import Role
from brokerops_core.ports.identity import AuthError, Principal

client = TestClient(app)


class _RejectingVerifier:
    async def verify(self, token: str | None) -> Principal:
        raise AuthError("not authenticated")


class _ViewerVerifier:
    async def verify(self, token: str | None) -> Principal:
        if token != "viewer-token":
            raise AuthError("bad token")
        return Principal(subject="v1", email="viewer@acme.com", role=Role.VIEWER)


@pytest.fixture
def cron_store() -> InMemoryCronRunStore:
    store = InMemoryCronRunStore()
    app.state.cron_run_store = store
    return store


def test_readyz_stays_minimal_and_public() -> None:
    # Fleet-upgrade + load balancers depend on unauthenticated {status: ok}.
    previous = app.state.identity_verifier
    app.state.identity_verifier = _RejectingVerifier()
    try:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
    finally:
        app.state.identity_verifier = previous


def test_statusz_demo_mode_returns_all_fields(cron_store: InMemoryCronRunStore) -> None:
    os.environ["IMAGE_VERSION"] = "v0.9.9-test"
    try:
        resp = client.get("/statusz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "v0.9.9-test"
        assert body["orchestrator"] == "langgraph"
        assert set(body["selectors"]) == {
            "crm",
            "email",
            "sms",
            "files",
            "extraction",
            "drafting",
        }
        assert body["migrations"]["matched"] is True
        assert body["migrations"]["mode"] == "no-database"
        assert body["last_cron"]["job"] == "milestones"
        assert body["last_cron"]["stale"] is True  # never run yet
        assert body["status"] == "degraded"  # cron never run → stale
        assert isinstance(body["uptime_seconds"], (int, float))
        assert body["uptime_seconds"] >= 0
    finally:
        os.environ.pop("IMAGE_VERSION", None)


def test_statusz_selector_summary_is_names_only(
    cron_store: InMemoryCronRunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Plant secret-shaped env vars; the summary must never echo them.
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    monkeypatch.setenv("SES_ACCESS_KEY_ID", "AKIASECRETLOOKALIKE")
    monkeypatch.setenv("SES_SECRET_ACCESS_KEY", "supersecretvalue")
    monkeypatch.setenv("FUB_API_KEY", "fub-live-key-should-not-leak")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@db/brokerops")
    monkeypatch.setenv("CRON_SECRET", "cron-secret-value")
    monkeypatch.setenv("CRM_VENDOR", "followupboss")

    summary = selector_summary()
    blob = json.dumps(summary)
    assert "AKIA" not in blob
    assert "supersecret" not in blob
    assert "fub-live" not in blob
    assert "sk-ant" not in blob
    assert "password" not in blob
    assert "cron-secret" not in blob
    assert summary["email"] == "ses"
    assert summary["crm"] == "followupboss"

    resp = client.get("/statusz")
    assert resp.status_code == 200
    wire = json.dumps(resp.json())
    assert "AKIA" not in wire
    assert "supersecret" not in wire
    assert "fub-live" not in wire
    assert "sk-ant" not in wire
    assert "password@" not in wire
    assert "cron-secret-value" not in wire


def test_statusz_requires_auth_or_internal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = app.state.identity_verifier
    app.state.identity_verifier = _RejectingVerifier()
    monkeypatch.delenv("STATUS_INTERNAL_KEY", raising=False)
    try:
        assert client.get("/statusz").status_code == 401
    finally:
        app.state.identity_verifier = previous


def test_statusz_accepts_internal_status_key(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = app.state.identity_verifier
    app.state.identity_verifier = _RejectingVerifier()
    monkeypatch.setenv("STATUS_INTERNAL_KEY", "status-probe-key")
    try:
        denied = client.get("/statusz")
        assert denied.status_code == 401
        bad = client.get("/statusz", headers={"X-Status-Key": "wrong"})
        assert bad.status_code == 401
        ok = client.get("/statusz", headers={"X-Status-Key": "status-probe-key"})
        assert ok.status_code == 200
        assert "selectors" in ok.json()
    finally:
        app.state.identity_verifier = previous
        monkeypatch.delenv("STATUS_INTERNAL_KEY", raising=False)


def test_statusz_accepts_viewer_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = app.state.identity_verifier
    app.state.identity_verifier = _ViewerVerifier()
    monkeypatch.delenv("STATUS_INTERNAL_KEY", raising=False)
    try:
        assert client.get("/statusz").status_code == 401
        resp = client.get("/statusz", headers={"Authorization": "Bearer viewer-token"})
        assert resp.status_code == 200
    finally:
        app.state.identity_verifier = previous


class _SubViewerPrincipal:
    """A principal whose role.allows(VIEWER) is False — the hierarchy has no
    real rank below viewer, so the gate's fail-closed branch is exercised with
    a stand-in that refuses VIEWER (and everything else)."""

    def __init__(self) -> None:
        self.value = "none"

    def allows(self, required: Role) -> bool:  # noqa: ARG002
        return False


class _SubViewerVerifier:
    async def verify(self, token: str | None) -> Principal:
        if token != "subviewer-token":
            raise AuthError("bad token")
        principal = Principal(subject="s1", email="none@acme.com", role=Role.VIEWER)
        # Swap the role object after construction so the gate sees allows()=False.
        object.__setattr__(principal, "role", _SubViewerPrincipal())
        return principal


def test_statusz_denies_sub_viewer_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = app.state.identity_verifier
    app.state.identity_verifier = _SubViewerVerifier()
    monkeypatch.delenv("STATUS_INTERNAL_KEY", raising=False)
    try:
        resp = client.get("/statusz", headers={"Authorization": "Bearer subviewer-token"})
        assert resp.status_code == 403
        assert "viewer" in resp.json()["detail"]
    finally:
        app.state.identity_verifier = previous


@pytest.mark.asyncio
async def test_cron_staleness_detectable_from_payload() -> None:
    store = InMemoryCronRunStore()
    # Never run → stale.
    payload = await build_status_payload(started_at=0.0, cron_store=store, migration_engine=None)
    assert payload["last_cron"]["stale"] is True
    assert payload["status"] == "degraded"

    await store.record("milestones", outcome="success", checked=2)
    # Fresh success → not stale.
    payload = await build_status_payload(started_at=0.0, cron_store=store, migration_engine=None)
    assert payload["last_cron"]["stale"] is False
    assert payload["last_cron"]["outcome"] == "success"
    assert payload["last_cron"]["checked"] == 2
    assert payload["status"] == "ok"

    # Old run → stale.
    old = CronRunRecord(
        job="milestones",
        outcome="success",
        finished_at=datetime.now(UTC) - timedelta(seconds=CRON_STALE_AFTER_SECONDS + 10),
        checked=1,
    )
    store._rows["milestones"] = old
    payload = await build_status_payload(started_at=0.0, cron_store=store, migration_engine=None)
    assert payload["last_cron"]["stale"] is True
    assert payload["status"] == "degraded"


def test_cron_endpoint_records_success_outcome(cron_store: InMemoryCronRunStore) -> None:
    # Empty active set is fine — still records a successful run with checked=0.
    # Need engine/store wired; use app defaults from lifespan-less TestClient
    # which still resolves cron_run_store from app.state.
    from brokerops_api.db import (
        InMemoryApprovalRepo,
        InMemoryTransactionStore,
    )
    from brokerops_api.deps import (
        get_approval_repo,
        get_transaction_store,
        get_workflow_engine,
    )

    class _Engine:
        async def start(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("no active transactions expected")

    app.dependency_overrides[get_workflow_engine] = lambda: _Engine()
    app.dependency_overrides[get_transaction_store] = lambda: InMemoryTransactionStore()
    app.dependency_overrides[get_approval_repo] = lambda: InMemoryApprovalRepo()
    try:
        resp = client.post("/internal/cron/milestones")
        assert resp.status_code == 200
        assert resp.json()["checked"] == 0
    finally:
        app.dependency_overrides.clear()

    latest = cron_store._rows.get("milestones")
    assert latest is not None
    assert latest.outcome == "success"
    assert latest.checked == 0


def test_json_log_formatter_emits_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_VERSION", "v1.2.3")
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET /readyz 200",
        args=(),
        exc_info=None,
    )
    line = formatter.format(record)
    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["message"] == "GET /readyz 200"
    assert payload["logger"] == "uvicorn.access"
    assert payload["service"] == "brokerops-api"
    assert payload["version"] == "v1.2.3"
    assert "time" in payload


def test_json_logging_enabled_on_cloud_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    monkeypatch.setenv("K_SERVICE", "brokerops-demo-api")
    assert json_logging_enabled() is True
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("LOG_FORMAT", "text")
    assert json_logging_enabled() is False
    monkeypatch.setenv("LOG_FORMAT", "json")
    assert json_logging_enabled() is True


def test_cron_payload_failure_is_stale() -> None:
    record = CronRunRecord(
        job="milestones",
        outcome="failure",
        finished_at=datetime.now(UTC),
        error="RuntimeError",
    )
    body = cron_payload(record)
    assert body["stale"] is True
    assert body["error"] == "RuntimeError"


def test_safe_error_label_never_embeds_message() -> None:
    from brokerops_api.status import safe_error_label

    class Boom(Exception):
        pass

    label = safe_error_label(Boom("postgresql://user:password@db/brokerops key=sk-ant-secret"))
    assert label == "Boom"
    assert "password" not in label
    assert "sk-ant" not in label
