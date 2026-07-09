"""The transaction hub's read-composition filters (BOP-027).

Drives the real lifespan wiring (`with TestClient(app)`, exactly what `docker
compose up` runs credential-free) so the recording seam populates the audit
ledger, then asserts the three transaction-scoped read filters the hub composes
from — comms, approvals, and the audit slice — each confined to one deal.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brokerops_api.main import app
from brokerops_core.ports.identity import AuthError, Principal, Role


class _StubRoleVerifier:
    """Maps the bearer ('viewer'/'operator'/'admin') to a principal of that role,
    so a route's caller-role response filtering can be exercised (mirrors test_rbac)."""

    async def verify(self, token: str | None) -> Principal:
        if token in {"viewer", "operator", "admin"}:
            return Principal(subject=f"{token}@x.com", email=f"{token}@x.com", role=Role(token))
        raise AuthError("bad token")


@pytest.fixture(autouse=True)
def _in_process_crm(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The audit slice needs the real recording seam to fire, so drive the full
    # lifespan (not an engine override). `internal` selects the in-process FUB
    # stub — the zero-credential CRM the demo uses — so the cron's CRM writes land
    # in the ledger without reaching the real FollowUpBoss API.
    monkeypatch.setenv("FUB_BASE_URL", "internal")
    yield


def _seed_and_run(client: TestClient) -> dict[str, dict[str, Any]]:
    """Seed the demo, run the milestone cron, and decide both raised gates.

    Returns the pending approvals by kind after the cron run (before deciding),
    so a caller can address TXN-1001's escalation and TXN-1002's email gate.
    """
    assert client.post("/demo/seed", json={"reset": True}).status_code == 200
    client.post("/internal/cron/milestones")
    pending = client.get("/approvals").json()
    return {a["kind"]: a for a in pending}


def test_hub_filters_are_each_scoped_to_one_transaction() -> None:
    with TestClient(app) as client:
        gates = _seed_and_run(client)
        escalation = gates["approve_escalation"]  # TXN-1001
        email_gate = gates["approve_outbound_message"]  # TXN-1002
        assert escalation["payload"]["transaction_id"] == "TXN-1001"
        assert email_gate["payload"]["transaction_id"] == "TXN-1002"

        # Approve the escalation → an URGENT CRM task lands in the ledger under
        # TXN-1001's run; approve the email → a send crosses the boundary tagged
        # with TXN-1002.
        client.post(f"/approvals/{escalation['id']}/decide", json={"decision": "approved"})
        decided_email = client.post(
            f"/approvals/{email_gate['id']}/decide", json={"decision": "approved"}
        ).json()
        message_id = decided_email["workflow"]["output"]["reminder_message_id"]

        # ── comms: /messages?transaction_id ──────────────────────────────────
        comms_1002 = client.get("/messages?transaction_id=TXN-1002").json()
        assert [m["id"] for m in comms_1002] == [message_id]
        assert comms_1002[0]["status"] == "sent"
        # The reminder belongs to TXN-1002 only — TXN-1001 has no comms.
        assert client.get("/messages?transaction_id=TXN-1001").json() == []

        # ── approvals: /approvals?transaction_id (pending AND decided) ────────
        approvals_1001 = client.get("/approvals?transaction_id=TXN-1001").json()
        assert [a["id"] for a in approvals_1001] == [escalation["id"]]
        # The default inbox is pending-only, so a decided escalation would be
        # absent there — the transaction filter still surfaces it.
        assert approvals_1001[0]["status"] == "approved"
        assert escalation["id"] not in {a["id"] for a in client.get("/approvals").json()}

        # ── audit slice: /audit?transaction_id ───────────────────────────────
        audit_1001 = client.get("/audit?transaction_id=TXN-1001").json()
        tools_1001 = {r["tool"] for r in audit_1001}
        assert "create_task" in tools_1001  # the URGENT escalation task
        assert all(r["workflow"] == "transaction_coordination" for r in audit_1001)
        # The TXN-1002 send must not bleed into TXN-1001's slice.
        assert "send_email" not in tools_1001

        audit_1002 = client.get("/audit?transaction_id=TXN-1002").json()
        sends = [r for r in audit_1002 if r["tool"] == "send_email"]
        assert len(sends) == 1
        assert sends[0]["transaction_id"] == "TXN-1002"


def test_audit_slice_includes_run_writes_before_any_approval_is_decided() -> None:
    # BOP-027 review r2: the slice is complete for writes from a transaction's run
    # regardless of an approval's existence/decision. TXN-1002's due-soon run
    # creates CRM reminder tasks (send_reminders) BEFORE its outbound-message gate;
    # right after cron — nothing decided, the gate still pending — those writes
    # must already be in the deal's audit slice, stamped with its transaction_id.
    with TestClient(app) as client:
        _seed_and_run(client)
        pending = client.get("/approvals?transaction_id=TXN-1002").json()
        assert [a["status"] for a in pending] == ["pending"]  # nothing decided yet

        slice_1002 = client.get("/audit?transaction_id=TXN-1002").json()
        reminder_writes = [r for r in slice_1002 if r["tool"] == "create_task"]
        assert reminder_writes  # present with no decided approval for the run
        assert all(r["transaction_id"] == "TXN-1002" for r in slice_1002)


def test_viewer_open_hub_reads_never_deliver_body_or_args_over_the_wire() -> None:
    # BOP-027 review r4: the hub's reads are viewer-open, but the RESPONSE PAYLOAD
    # (not just the DOM) must exclude the message body and the raw mutation args for
    # a viewer — role-filtered server-side, so a viewer never receives the content.
    with TestClient(app) as client:
        gates = _seed_and_run(client)
        # Send the reminder so a body-bearing message + a send record both exist.
        client.post(
            f"/approvals/{gates['approve_outbound_message']['id']}/decide",
            json={"decision": "approved"},
        )
        original = app.state.identity_verifier
        app.state.identity_verifier = _StubRoleVerifier()
        try:
            viewer = {"Authorization": "Bearer viewer"}
            operator = {"Authorization": "Bearer operator"}

            # ── comms: body redacted for a viewer, intact for an operator ──
            v_msgs = client.get("/messages?transaction_id=TXN-1002", headers=viewer).json()
            assert v_msgs and all(m["body"] == "[redacted:pii]" for m in v_msgs)
            assert all(m["subject"] == "[redacted:pii]" for m in v_msgs)  # freeform content
            assert all(m["recipient"] == "[redacted:pii]" for m in v_msgs)  # CONTACT_PII too
            o_msgs = client.get("/messages?transaction_id=TXN-1002", headers=operator).json()
            assert any(m["body"] and m["body"] != "[redacted:pii]" for m in o_msgs)
            assert all(m["recipient"] != "[redacted:pii]" for m in o_msgs)

            # ── audit: args redacted for a viewer, present for an operator ──
            v_audit = client.get("/audit?transaction_id=TXN-1002", headers=viewer).json()
            assert v_audit and all(r["args"] is None for r in v_audit)
            # …but the action metadata a review surface needs is still there.
            assert all(r["tool"] and r["outcome"] for r in v_audit)
            o_audit = client.get("/audit?transaction_id=TXN-1002", headers=operator).json()
            assert any(r["args"] for r in o_audit)

            # ── approvals: the gate's draft payload redacted for a viewer ──
            v_appr = client.get("/approvals?transaction_id=TXN-1002", headers=viewer).json()
            assert v_appr and all(a["payload"] is None for a in v_appr)
            # …the viewer still sees a gate EXISTS (kind/status), to link out from.
            assert all(a["kind"] and a["status"] for a in v_appr)
            o_appr = client.get("/approvals?transaction_id=TXN-1002", headers=operator).json()
            assert any(a["payload"] for a in o_appr)

            # ── transaction detail: party contact email redacted for a viewer ──
            v_txn = client.get("/transactions/TXN-1002", headers=viewer).json()
            v_emails = [p["email"] for p in v_txn["transaction"]["parties"]]
            assert v_emails and all(e in ("", "[redacted:pii]") for e in v_emails)
            o_txn = client.get("/transactions/TXN-1002", headers=operator).json()
            assert any("@" in p["email"] for p in o_txn["transaction"]["parties"])
        finally:
            app.state.identity_verifier = original


def test_unknown_transaction_yields_empty_slices() -> None:
    with TestClient(app) as client:
        _seed_and_run(client)
        assert client.get("/messages?transaction_id=NOPE-999").json() == []
        assert client.get("/approvals?transaction_id=NOPE-999").json() == []
        assert client.get("/audit?transaction_id=NOPE-999").json() == []
