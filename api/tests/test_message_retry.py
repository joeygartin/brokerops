"""The FAILED-approval retry path + decide-route hardening (BOP-037).

The gap: an approved outbound-message gate whose provider send FAILED leaves the
approval APPROVED (the decide route 409s forever) and the row FAILED, with no
surface able to reach `send_approved`'s FAILED→SENT transition. These tests
drive that exact scenario end-to-end — provider outage at approve → FAILED row →
`POST /messages/{id}/retry` → SENT, audit-linked to the original approval — plus
the decide route's new boundary behaviors: a typed 422 for hostile
edited_payloads and a clean 409 (not a 500) for a dangling message row.
"""

from collections.abc import Iterator
from itertools import count
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import (
    InMemoryApprovalRepo,
    InMemoryAuditLog,
    InMemoryFeedbackStore,
    InMemoryMessageStore,
)
from brokerops_api.deps import (
    get_approval_repo,
    get_audit_log,
    get_feedback_store,
    get_message_service,
    get_message_store,
    get_workflow_engine,
)
from brokerops_api.main import app
from brokerops_api.workflows import VAPI_FOLLOWUP
from brokerops_core.models.message import Message
from brokerops_core.services.audit import RecordingEmail
from brokerops_core.services.drafting import DeterministicDrafter
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_core.services.message_send import MessageSendService
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app
from brokerops_langgraph.engine import LangGraphWorkflowEngine
from brokerops_langgraph.graphs.vapi_followup import build_vapi_followup
from brokerops_vapi.adapter import VapiVoiceAdapter
from brokerops_vapi.stub import RECORDED_TRANSCRIPTS, create_stub_app as create_vapi_stub

fub_stub = create_stub_app()


def _stub_crm() -> FUBCRMAdapter:
    transport = httpx.ASGITransport(app=fub_stub)
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=client)


def _stub_voice() -> VapiVoiceAdapter:
    transport = httpx.ASGITransport(app=create_vapi_stub())
    client = httpx.AsyncClient(transport=transport, base_url="http://vapi.test")
    return VapiVoiceAdapter(api_key="stub-key", base_url="http://vapi.test", client=client)


class OutageEmail:
    """EmailPort double with a provider-outage switch."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.down = False
        self._ids = count(1)

    async def send(self, message: Message) -> str:
        if self.down:
            raise RuntimeError("provider outage")
        self.sent.append(message)
        return f"provider-{next(self._ids)}"


feedback_store = InMemoryFeedbackStore()
repo = InMemoryApprovalRepo()
message_store = InMemoryMessageStore()
audit_log = InMemoryAuditLog()
email = OutageEmail()
# RecordingEmail so retry sends land in the ledger with their approval linkage.
message_service = MessageSendService(
    email=RecordingEmail(email, audit_log),
    store=message_store,
    drafting=DeterministicDrafter(),
)
engine = LangGraphWorkflowEngine(
    {
        VAPI_FOLLOWUP: build_vapi_followup(
            _stub_voice(),
            _stub_crm(),
            feedback_store,
            DeterministicExtractor(),
            message_service,
            InMemorySaver(),
        )
    },
    repo,
)
# The approve step intentionally blows up mid-request (provider outage), so the
# module client must surface it as a 500 response, not re-raise into the test.
client = TestClient(app, raise_server_exceptions=False)

WEBHOOK_SECRET = "test-secret"
SIGNED = {"x-vapi-secret": WEBHOOK_SECRET}


@pytest.fixture(autouse=True)
def _wire_overrides(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", WEBHOOK_SECRET)
    email.down = False
    email.sent.clear()
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_approval_repo] = lambda: repo
    app.dependency_overrides[get_feedback_store] = lambda: feedback_store
    app.dependency_overrides[get_message_store] = lambda: message_store
    app.dependency_overrides[get_message_service] = lambda: message_service
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    yield
    app.dependency_overrides.clear()


def _end_of_call(call_id: str) -> dict[str, Any]:
    return {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": {
                "id": call_id,
                "metadata": {"listing_key": "RM1001", "contact_id": "101"},
            },
            "artifact": {"transcript": RECORDED_TRANSCRIPTS["cool"]},
        }
    }


def _outbound_gate(call_id: str) -> dict[str, Any]:
    """Drive the cool-call flow to its approve-outbound-message gate."""
    body = client.post("/webhooks/vapi", json=_end_of_call(call_id), headers=SIGNED).json()
    assert body["status"] == "awaiting_approval"
    gate = client.get(f"/approvals/{body['approval_id']}").json()
    assert gate["kind"] == "approve_outbound_message"
    result: dict[str, Any] = gate
    return result


def test_provider_outage_then_retry_completes_the_approved_send() -> None:
    gate = _outbound_gate("call-retry-1")
    message_id = gate["payload"]["message_id"]

    # 1. The human approves while the provider is down: the decision lands
    #    (approval → approved) but the send dies — the row is FAILED.
    email.down = True
    outage = client.post(f"/approvals/{gate['id']}/decide", json={"decision": "approved"})
    assert outage.status_code == 500
    assert client.get(f"/approvals/{gate['id']}").json()["status"] == "approved"
    assert client.get(f"/messages/{message_id}").json()["status"] == "failed"

    # 2. The decide route can never reach the send again — 409 forever.
    stuck = client.post(f"/approvals/{gate['id']}/decide", json={"decision": "approved"})
    assert stuck.status_code == 409

    # 3. The retry route drives the same decision to completion.
    email.down = False
    retried = client.post(f"/messages/{message_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "sent"
    assert client.get(f"/messages/{message_id}").json()["status"] == "sent"
    assert [m.id for m in email.sent] == [message_id]

    # 4. The retry send is in the ledger, linked to the ORIGINAL approval.
    records = [
        r
        for r in client.get("/audit").json()
        if r["workflow"] == "message_retry" and r["outcome"] == "success"
    ]
    assert len(records) == 1
    assert records[0]["approval_id"] == gate["id"]
    assert records[0]["tool"] == "send_email"


def test_retry_is_failed_only_and_missing_is_404() -> None:
    gate = _outbound_gate("call-retry-2")
    message_id = gate["payload"]["message_id"]
    # PENDING_APPROVAL: retry must refuse — it may never bypass the human gate.
    pending = client.post(f"/messages/{message_id}/retry")
    assert pending.status_code == 409
    assert "pending_approval" in pending.json()["detail"]
    assert client.get(f"/messages/{message_id}").json()["status"] == "pending_approval"
    assert email.sent == []
    # SENT (after a clean approve): a replayed retry must not re-send.
    decided = client.post(f"/approvals/{gate['id']}/decide", json={"decision": "approved"})
    assert decided.status_code == 200
    sends_after_approve = len(email.sent)
    replay = client.post(f"/messages/{message_id}/retry")
    assert replay.status_code == 409
    assert len(email.sent) == sends_after_approve
    # unknown id
    assert client.post("/messages/nope/retry").status_code == 404


def test_retry_surfaces_a_repeat_provider_failure_as_502() -> None:
    gate = _outbound_gate("call-retry-3")
    message_id = gate["payload"]["message_id"]
    email.down = True
    assert (
        client.post(f"/approvals/{gate['id']}/decide", json={"decision": "approved"}).status_code
        == 500
    )
    still_down = client.post(f"/messages/{message_id}/retry")
    assert still_down.status_code == 502
    assert "provider send failed" in still_down.json()["detail"]
    assert client.get(f"/messages/{message_id}").json()["status"] == "failed"  # retryable again


def test_decide_maps_a_dangling_message_row_to_409() -> None:
    # BOP-037: the gate's row vanishing is a state conflict — a clean 409 with
    # the domain error's message, not an AssertionError 500.
    gate = _outbound_gate("call-retry-4")
    message_id = gate["payload"]["message_id"]
    del message_store._messages[message_id]
    response = client.post(f"/approvals/{gate['id']}/decide", json={"decision": "approved"})
    assert response.status_code == 409
    assert message_id in response.json()["detail"]


def test_decide_validates_the_outbound_edited_payload_at_the_boundary() -> None:
    # BOP-037: hostile edited_payloads are a 422 at the boundary — never a
    # FAILED row + 500 halfway through a provider send.
    gate = _outbound_gate("call-retry-5")
    hostile: list[dict[str, Any]] = [
        {"subject": "Re: tour\r\nBcc: victim@example.test"},  # subject not admitted at all
        {"body": "with a \x00 control char"},
        {"body": "   "},
        {"unexpected": "field"},
    ]
    for payload in hostile:
        response = client.post(
            f"/approvals/{gate['id']}/decide",
            json={"decision": "approved", "edited_payload": payload},
        )
        assert response.status_code == 422, payload
    # nothing sent, gate still pending — the operator can decide for real
    assert email.sent == []
    assert client.get(f"/approvals/{gate['id']}").json()["status"] == "pending"
    # and a legitimate body edit still flows through and ships verbatim
    ok = client.post(
        f"/approvals/{gate['id']}/decide",
        json={"decision": "approved", "edited_payload": {"body": "Edited, then sent."}},
    )
    assert ok.status_code == 200
    assert email.sent[-1].body == "Edited, then sent."
