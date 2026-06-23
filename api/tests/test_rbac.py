"""Role enforcement at the API edge.

Unit: require_role admits/denies by hierarchy. Integration: the gated write
routes return 403 below the required role and pass the gate at or above it. A
stub verifier maps a bearer to a role so we don't mint real tokens here.
"""

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from brokerops_api.deps import (
    get_approval_repo,
    get_crm_port,
    get_voice_port,
    get_workflow_engine,
    require_role,
)
from brokerops_api.db import InMemoryApprovalRepo
from brokerops_api.main import app
from brokerops_core.ports.identity import AuthError, Principal, Role
from brokerops_core.services.workflow_runs import WorkflowRunResult

client = TestClient(app)


# ── unit: the guard itself ────────────────────────────────────────────────


def _p(role: Role) -> Principal:
    return Principal(subject="x@x.com", email="x@x.com", role=role)


def test_require_role_admits_at_or_above() -> None:
    guard = require_role(Role.OPERATOR)
    assert guard(_p(Role.OPERATOR)).role is Role.OPERATOR
    assert guard(_p(Role.ADMIN)).role is Role.ADMIN


def test_require_role_denies_below_with_403() -> None:
    guard = require_role(Role.ADMIN)
    with pytest.raises(HTTPException) as exc:
        guard(_p(Role.OPERATOR))
    assert exc.value.status_code == 403


# ── integration: the gated routes ─────────────────────────────────────────


class StubRoleVerifier:
    """Maps the bearer ('admin'/'operator'/'viewer') to a principal of that role."""

    async def verify(self, token: str | None) -> Principal:
        if token in {"admin", "operator", "viewer"}:
            return Principal(subject=f"{token}@x.com", email=f"{token}@x.com", role=Role(token))
        raise AuthError("bad token")


class StubEngine:
    async def start(
        self, name: str, payload: dict[str, object], actor: str | None = None
    ) -> WorkflowRunResult:
        return WorkflowRunResult(thread_id="t1", status="started")


@pytest.fixture(autouse=True)
def _wire() -> Iterator[None]:
    original = app.state.identity_verifier
    app.state.identity_verifier = StubRoleVerifier()
    app.dependency_overrides[get_approval_repo] = lambda: InMemoryApprovalRepo()
    app.dependency_overrides[get_workflow_engine] = lambda: StubEngine()
    # The outbound-call route resolves voice/crm deps from app.state (unset under
    # TestClient); stub them so a below-role caller is rejected at the gate, not by
    # a missing-dependency error.
    app.dependency_overrides[get_voice_port] = lambda: object()
    app.dependency_overrides[get_crm_port] = lambda: object()
    yield
    app.state.identity_verifier = original
    app.dependency_overrides.clear()


def _auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {role}"}


def test_decide_requires_admin() -> None:
    body = {"decision": "approved"}
    # admin clears the gate (then 404 — no such approval); others are 403.
    assert (
        client.post("/approvals/none/decide", json=body, headers=_auth("admin")).status_code == 404
    )
    assert (
        client.post("/approvals/none/decide", json=body, headers=_auth("operator")).status_code
        == 403
    )
    assert (
        client.post("/approvals/none/decide", json=body, headers=_auth("viewer")).status_code == 403
    )


def test_workflow_start_requires_operator() -> None:
    body = {"listing_key": "L1"}
    assert (
        client.post(
            "/workflows/listing-to-contract/start", json=body, headers=_auth("viewer")
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/workflows/listing-to-contract/start", json=body, headers=_auth("operator")
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/workflows/listing-to-contract/start", json=body, headers=_auth("admin")
        ).status_code
        == 202
    )


def test_outbound_call_requires_operator() -> None:
    body = {"listing_key": "L1", "contact_id": "c1"}
    # viewer is blocked at the gate before any handler work.
    assert client.post("/calls/outbound", json=body, headers=_auth("viewer")).status_code == 403


def test_reads_open_to_viewer() -> None:
    assert client.get("/approvals", headers=_auth("viewer")).status_code == 200
    assert client.get("/auth/me", headers=_auth("viewer")).json()["role"] == "viewer"
