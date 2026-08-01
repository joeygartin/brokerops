"""Wire-level caller-role egress regressions for every filtered read route (BOP-040).

Payload-level (asserts the JSON response body, never the DOM): for each viewer-open
read route whose response carries a restricted field, a viewer must NOT receive that
field over the wire while an operator does. Covers the routes BOP-040 closed beyond the
four transaction-hub reads BOP-027 already proved (``test_transaction_hub``):
``/approvals`` (default inbox + permalink), ``/messages`` (default list + single),
``/audit`` (default), ``/contacts`` (list + single), ``/transactions`` (list + detail),
and ``/calls/{id}`` (transcript). Redaction markers come from the shared egress filter:
a redacted string reads ``"[redacted:pii]"``; a redacted dict field reads ``null``.

Seeding runs under the credential-free demo identity (ADMIN — full data, AC #4), then the
identity verifier is swapped for a role-stub so the same rows are re-read as a viewer vs
an operator.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brokerops_api.deps import get_feedback_store
from brokerops_api.main import app
from brokerops_core.models.call import CallRecord
from brokerops_core.ports.identity import AuthError, Principal, Role

REDACTED = "[redacted:pii]"


class _StubRoleVerifier:
    """Maps the bearer ('viewer'/'operator'/'admin') to a principal of that role,
    so a route's caller-role response filtering can be exercised (mirrors test_rbac)."""

    async def verify(self, token: str | None) -> Principal:
        if token in {"viewer", "operator", "admin"}:
            return Principal(subject=f"{token}@x.com", email=f"{token}@x.com", role=Role(token))
        raise AuthError("bad token")


VIEWER = {"Authorization": "Bearer viewer"}
OPERATOR = {"Authorization": "Bearer operator"}


@pytest.fixture(autouse=True)
def _in_process_crm(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # `internal` selects the in-process FUB stub (the zero-credential CRM the demo
    # uses): the cron's CRM writes land in the ledger and /contacts reads resolve
    # without reaching the real FollowUpBoss API.
    monkeypatch.setenv("FUB_BASE_URL", "internal")
    yield


def _seed_and_cron(client: TestClient) -> None:
    # Under the default demo identity (ADMIN): seed the portfolio and run the
    # milestone cron so it raises the approval gates and records the audit slice.
    assert client.post("/demo/seed", json={"reset": True}).status_code == 200
    client.post("/internal/cron/milestones")


def _role_stub() -> None:
    app.state.identity_verifier = _StubRoleVerifier()


def test_approvals_inbox_and_permalink_redact_payload_for_a_viewer() -> None:
    with TestClient(app) as client:
        _seed_and_cron(client)
        original = app.state.identity_verifier
        _role_stub()
        try:
            # Default inbox list.
            v_inbox = client.get("/approvals", headers=VIEWER).json()
            assert v_inbox  # the read is open to a viewer…
            assert all(a["payload"] is None for a in v_inbox)  # …but the draft payload is gone
            assert all(a["kind"] and a["status"] for a in v_inbox)  # kind/status still visible

            o_inbox = client.get("/approvals", headers=OPERATOR).json()
            assert any(a["payload"] for a in o_inbox)  # operator sees the payload intact

            # Permalink (GET /approvals/{id}) for the same gate.
            gate_id = o_inbox[0]["id"]
            assert client.get(f"/approvals/{gate_id}", headers=VIEWER).json()["payload"] is None
            assert client.get(f"/approvals/{gate_id}", headers=OPERATOR).json()["payload"]
        finally:
            app.state.identity_verifier = original


def test_messages_list_and_single_redact_body_subject_recipient_for_a_viewer() -> None:
    with TestClient(app) as client:
        _seed_and_cron(client)
        # Approve the outbound-message gate (default ADMIN identity) so a real
        # Message row with a body exists to read back.
        pending = {a["kind"]: a for a in client.get("/approvals").json()}
        gate = pending["approve_outbound_message"]
        decided = client.post(
            f"/approvals/{gate['id']}/decide", json={"decision": "approved"}
        ).json()
        message_id = decided["workflow"]["output"]["reminder_message_id"]

        original = app.state.identity_verifier
        _role_stub()
        try:
            v_msgs = client.get("/messages", headers=VIEWER).json()
            assert v_msgs
            assert all(m["body"] == REDACTED for m in v_msgs)
            assert all(m["subject"] in ("", REDACTED) for m in v_msgs)
            assert all(m["recipient"] == REDACTED for m in v_msgs)  # CONTACT_PII

            o_msgs = client.get("/messages", headers=OPERATOR).json()
            assert any(m["body"] and m["body"] != REDACTED for m in o_msgs)
            assert all(m["recipient"] != REDACTED for m in o_msgs)

            v_one = client.get(f"/messages/{message_id}", headers=VIEWER).json()
            assert v_one["body"] == REDACTED and v_one["recipient"] == REDACTED
            o_one = client.get(f"/messages/{message_id}", headers=OPERATOR).json()
            assert o_one["body"] and o_one["body"] != REDACTED
        finally:
            app.state.identity_verifier = original


def test_audit_list_redacts_mutation_args_for_a_viewer() -> None:
    with TestClient(app) as client:
        _seed_and_cron(client)
        original = app.state.identity_verifier
        _role_stub()
        try:
            v_audit = client.get("/audit", headers=VIEWER).json()
            assert v_audit  # viewer-open
            assert all(r["args"] is None for r in v_audit)  # RESTRICTED_CONTENT → null

            o_audit = client.get("/audit", headers=OPERATOR).json()
            assert any(r["args"] for r in o_audit)  # operator sees the argument snapshot
        finally:
            app.state.identity_verifier = original


def test_transactions_list_and_detail_redact_party_email_for_a_viewer() -> None:
    with TestClient(app) as client:
        _seed_and_cron(client)
        original = app.state.identity_verifier
        _role_stub()
        try:

            def party_emails(rows: list[dict[str, Any]]) -> list[str]:
                return [p["email"] for row in rows for p in row["transaction"]["parties"]]

            v_list = client.get("/transactions", headers=VIEWER).json()
            v_emails = party_emails(v_list)
            assert v_emails and all(e in ("", REDACTED) for e in v_emails)
            assert REDACTED in v_emails  # a real email WAS present and got redacted

            o_list = client.get("/transactions", headers=OPERATOR).json()
            assert any("@" in e for e in party_emails(o_list))

            txn_id = v_list[0]["transaction"]["id"]
            v_detail = client.get(f"/transactions/{txn_id}", headers=VIEWER).json()
            assert all(p["email"] in ("", REDACTED) for p in v_detail["transaction"]["parties"])
            o_detail = client.get(f"/transactions/{txn_id}", headers=OPERATOR).json()
            assert any("@" in p["email"] for p in o_detail["transaction"]["parties"])
        finally:
            app.state.identity_verifier = original


def test_contacts_list_and_single_redact_email_and_phone_for_a_viewer() -> None:
    with TestClient(app) as client:
        original = app.state.identity_verifier
        _role_stub()
        try:
            v_list = client.get("/contacts", params={"q": "jordan"}, headers=VIEWER).json()
            assert v_list  # viewer-open
            assert all(c["email"] in (None, REDACTED) for c in v_list)
            assert all(c["phone"] in (None, REDACTED) for c in v_list)
            assert any(c["email"] == REDACTED for c in v_list)  # a real email got redacted

            o_list = client.get("/contacts", params={"q": "jordan"}, headers=OPERATOR).json()
            assert any(c["email"] and "@" in c["email"] for c in o_list)

            contact_id = o_list[0]["crm_id"]
            v_one = client.get(f"/contacts/{contact_id}", headers=VIEWER).json()
            assert v_one["email"] in (None, REDACTED) and v_one["phone"] in (None, REDACTED)
            o_one = client.get(f"/contacts/{contact_id}", headers=OPERATOR).json()
            assert o_one["email"] and "@" in o_one["email"]
        finally:
            app.state.identity_verifier = original


class _StubFeedbackStore:
    """A feedback store that returns one call record — enough to read /calls/{id}."""

    async def get_call_record(self, call_id: str) -> CallRecord | None:
        return CallRecord(
            vapi_call_id=call_id,
            contact_id="101",
            listing_key="RM1001",
            transcript="Agent: Hi Jordan, following up on your tour. Client: I loved it.",
            outcome="interested",
        )


def test_call_record_redacts_transcript_for_a_viewer() -> None:
    with TestClient(app) as client:
        original = app.state.identity_verifier
        _role_stub()
        app.dependency_overrides[get_feedback_store] = lambda: _StubFeedbackStore()
        try:
            v_call = client.get("/calls/CALL-1", headers=VIEWER).json()
            assert v_call["transcript"] == REDACTED  # RESTRICTED_CONTENT
            assert v_call["outcome"] == "interested"  # non-restricted linkage still visible

            o_call = client.get("/calls/CALL-1", headers=OPERATOR).json()
            assert o_call["transcript"].startswith("Agent:")  # operator sees what was said
        finally:
            app.state.identity_verifier = original
            app.dependency_overrides.clear()
