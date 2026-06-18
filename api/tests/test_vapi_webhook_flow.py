"""The Phase 5 gate at the API level: end-of-call webhook → transcript →
structured feedback persisted → FUB note + call log; hot calls pause at the
notify-agent gate and approval creates the hot-lead task."""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import InMemoryApprovalRepo, InMemoryFeedbackStore
from brokerops_api.deps import get_approval_repo, get_feedback_store, get_workflow_engine
from brokerops_api.main import app
from brokerops_api.workflows import VAPI_FOLLOWUP
from brokerops_core.services.feedback_extraction import DeterministicExtractor
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


feedback_store = InMemoryFeedbackStore()
repo = InMemoryApprovalRepo()
engine = LangGraphWorkflowEngine(
    {
        VAPI_FOLLOWUP: build_vapi_followup(
            _stub_voice(), _stub_crm(), feedback_store, DeterministicExtractor(), InMemorySaver()
        )
    },
    repo,
)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _wire_overrides() -> Iterator[None]:
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_approval_repo] = lambda: repo
    app.dependency_overrides[get_feedback_store] = lambda: feedback_store
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
        "/webhooks/vapi", json=_end_of_call("call-cool-1", RECORDED_TRANSCRIPTS["cool"])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["processed"] is True
    assert body["status"] == "completed"

    feedback = client.get("/feedback", params={"listing_key": "RM1001"}).json()
    cool = next(f for f in feedback if f["call_id"] == "call-cool-1")
    assert cool["sentiment"] == "negative"
    assert cool["structured_answers"]["price_opinion"] == "overpriced"

    record = client.get("/calls/call-cool-1").json()
    assert "overpriced" in record["transcript"]
    assert record["extracted"]["sentiment"] == "negative"


def test_gate_hot_call_pauses_then_creates_hot_task() -> None:
    response = client.post(
        "/webhooks/vapi", json=_end_of_call("call-hot-1", RECORDED_TRANSCRIPTS["hot"])
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
        "/webhooks/vapi", json={"message": {"type": "status-update", "call": {"id": "x"}}}
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
