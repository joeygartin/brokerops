"""The Phase 5 gate at the API level: end-of-call webhook → transcript →
structured feedback persisted → FUB note + call log; hot calls pause at the
notify-agent gate and approval creates the hot-lead task; synced calls pause
at the drafted follow-up-email gate (BOP-019) — approve sends the (possibly
edited) draft, reject sends nothing."""

from collections.abc import Iterator
from itertools import count
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import InMemoryApprovalRepo, InMemoryFeedbackStore, InMemoryMessageStore
from brokerops_api.deps import (
    get_approval_repo,
    get_feedback_store,
    get_message_store,
    get_workflow_engine,
)
from brokerops_api.main import app
from brokerops_api.workflows import VAPI_FOLLOWUP
from brokerops_core.models.message import Message
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
    fub_client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=fub_client)


def _stub_voice() -> VapiVoiceAdapter:
    transport = httpx.ASGITransport(app=create_vapi_stub())
    vapi_client = httpx.AsyncClient(transport=transport, base_url="http://vapi.test")
    return VapiVoiceAdapter(api_key="stub-key", base_url="http://vapi.test", client=vapi_client)


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self._ids = count(1)

    async def send(self, message: Message) -> str:
        self.sent.append(message)
        return f"provider-{next(self._ids)}"


feedback_store = InMemoryFeedbackStore()
repo = InMemoryApprovalRepo()
message_store = InMemoryMessageStore()
email = FakeEmail()
message_service = MessageSendService(
    email=email, store=message_store, drafting=DeterministicDrafter()
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
client = TestClient(app)

# The webhook fails closed without a configured secret, so the flow tests run with one
# set and send the matching header on every signed POST.
WEBHOOK_SECRET = "test-secret"
SIGNED = {"x-vapi-secret": WEBHOOK_SECRET}


@pytest.fixture(autouse=True)
def _wire_overrides(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_approval_repo] = lambda: repo
    app.dependency_overrides[get_feedback_store] = lambda: feedback_store
    app.dependency_overrides[get_message_store] = lambda: message_store
    yield
    app.dependency_overrides.clear()


def _end_of_call(call_id: str, transcript: str) -> dict[str, Any]:
    return {
        "message": {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "call": {
                "id": call_id,
                "metadata": {"listing_key": "RM1001", "contact_id": "101"},
            },
            "artifact": {"transcript": transcript},
        }
    }


def test_gate_cool_call_to_feedback_and_fub_note() -> None:
    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call("call-cool-1", RECORDED_TRANSCRIPTS["cool"]),
        headers=SIGNED,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["processed"] is True
    # BOP-019: the synced path now drafts a follow-up email to the toured
    # contact (101 has an email in the CRM) and pauses at the outbound gate.
    assert body["status"] == "awaiting_approval"

    feedback = client.get("/feedback", params={"listing_key": "RM1001"}).json()
    cool = next(f for f in feedback if f["call_id"] == "call-cool-1")
    assert cool["sentiment"] == "negative"
    assert cool["structured_answers"]["price_opinion"] == "overpriced"

    record = client.get("/calls/call-cool-1").json()
    assert "overpriced" in record["transcript"]
    assert record["extracted"]["sentiment"] == "negative"

    gate = next(a for a in client.get("/approvals").json() if a["id"] == body["approval_id"])
    assert gate["kind"] == "approve_outbound_message"
    assert gate["payload"]["recipient"] == "jordan.pike@example.test"
    assert gate["payload"]["channel"] == "email"
    assert "tour" in gate["payload"]["subject"]

    # approve with an edited body — the edited-payload round trip: the edited
    # text is what ships and what the outbound_messages row records
    sends_before = len(email.sent)
    decided = client.post(
        f"/approvals/{gate['id']}/decide",
        json={
            "decision": "approved",
            "decided_by": "demo-operator",
            "edited_payload": {"body": "Thanks again for touring — edited before send."},
        },
    ).json()
    assert decided["workflow"]["status"] == "completed"
    assert decided["workflow"]["output"]["outcome"] == "followup_sent"
    row = client.get(f"/messages/{decided['workflow']['output']['followup_message_id']}").json()
    assert row["status"] == "sent"
    assert row["body"] == "Thanks again for touring — edited before send."
    assert len(email.sent) == sends_before + 1
    assert email.sent[-1].body == "Thanks again for touring — edited before send."


def test_gate_cool_call_followup_rejection_sends_nothing() -> None:
    body = client.post(
        "/webhooks/vapi",
        json=_end_of_call("call-cool-2", RECORDED_TRANSCRIPTS["cool"]),
        headers=SIGNED,
    ).json()
    assert body["status"] == "awaiting_approval"

    sends_before = len(email.sent)
    decided = client.post(
        f"/approvals/{body['approval_id']}/decide",
        json={"decision": "rejected", "decided_by": "demo-operator"},
    ).json()
    assert decided["workflow"]["status"] == "followup_dismissed"
    row = client.get(f"/messages/{decided['workflow']['output']['followup_message_id']}").json()
    assert row["status"] == "rejected"
    assert len(email.sent) == sends_before


def test_gate_hot_call_pauses_then_creates_hot_task() -> None:
    response = client.post(
        "/webhooks/vapi",
        json=_end_of_call("call-hot-1", RECORDED_TRANSCRIPTS["hot"]),
        headers=SIGNED,
    ).json()
    assert response["status"] == "awaiting_approval"
    approval_id = response["approval_id"]

    pending = client.get("/approvals").json()
    hot = next(a for a in pending if a["id"] == approval_id)
    assert hot["kind"] == "notify_agent"
    assert "offer intent" in hot["payload"]["reason"]
    assert hot["payload"]["summary"].startswith("Sentiment: positive")

    decided = client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "approved", "decided_by": "demo-operator"},
    ).json()
    assert decided["workflow"]["status"] == "completed"
    assert decided["workflow"]["output"]["outcome"] == "agent_notified"
    assert decided["workflow"]["output"]["hot_task_id"]

    feedback = client.get("/feedback", params={"listing_key": "RM1001"}).json()
    hot_feedback = next(f for f in feedback if f["call_id"] == "call-hot-1")
    assert hot_feedback["structured_answers"]["budget_min"] == 450_000


def test_non_end_of_call_messages_are_ignored() -> None:
    response = client.post(
        "/webhooks/vapi",
        json={"message": {"type": "status-update", "call": {"id": "x"}}},
        headers=SIGNED,
    )
    assert response.json() == {"ignored": True, "type": "status-update"}


def test_webhook_secret_enforced_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "hush")
    bad = client.post("/webhooks/vapi", json=_end_of_call("c", "t"))
    assert bad.status_code == 401
    good = client.post(
        "/webhooks/vapi",
        json={"message": {"type": "status-update"}},
        headers={"x-vapi-secret": "hush"},
    )
    assert good.status_code == 200


def test_webhook_fails_closed_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unset secret is a misconfigured deploy, not "auth off": the webhook must refuse
    # to start a workflow run rather than accept an unauthenticated POST.
    monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
    blocked = client.post("/webhooks/vapi", json=_end_of_call("c", "t"))
    assert blocked.status_code == 500


def test_webhook_rejects_repo_known_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    # "unset" is the repo-known Terraform placeholder — worthless as a shared secret. A
    # deploy that never got a real secret must fail closed (500), never accept x-vapi-secret
    # "unset" from an attacker who read the placeholder value out of the repo.
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "unset")
    blocked = client.post(
        "/webhooks/vapi", json=_end_of_call("c", "t"), headers={"x-vapi-secret": "unset"}
    )
    assert blocked.status_code == 500
