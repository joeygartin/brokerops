"""The Phase 4 gate at the API level: cron run → overdue milestone escalates
to the approvals inbox → approval creates an URGENT CRM task and ratchets the
escalation level. Also covers the due-soon drafted-reminder tail (BOP-019:
reminder fan-out then a drafted email paused at the outbound-message gate) and
pending-gate dedup."""

from collections.abc import Iterator
from itertools import count

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import (
    InMemoryApprovalRepo,
    InMemoryDocumentStore,
    InMemoryMessageStore,
    InMemoryTransactionStore,
)
from brokerops_api.deps import (
    get_approval_repo,
    get_document_store,
    get_message_store,
    get_transaction_store,
    get_transaction_store_admin,
    get_workflow_engine,
)
from brokerops_api.main import app
from brokerops_api.workflows import TRANSACTION_COORDINATION
from brokerops_core.models.message import Message
from brokerops_core.services.drafting import DeterministicDrafter
from brokerops_core.services.message_send import MessageSendService
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app
from brokerops_langgraph.engine import LangGraphWorkflowEngine
from brokerops_langgraph.graphs.transaction_coordination import build_transaction_coordination

fub_stub = create_stub_app()


def _stub_crm() -> FUBCRMAdapter:
    transport = httpx.ASGITransport(app=fub_stub)
    fub_client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=fub_client)


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self._ids = count(1)

    async def send(self, message: Message) -> str:
        self.sent.append(message)
        return f"provider-{next(self._ids)}"


store = InMemoryTransactionStore()
documents = InMemoryDocumentStore()
repo = InMemoryApprovalRepo()
message_store = InMemoryMessageStore()
email = FakeEmail()
message_service = MessageSendService(
    email=email, store=message_store, drafting=DeterministicDrafter()
)
engine = LangGraphWorkflowEngine(
    {
        TRANSACTION_COORDINATION: build_transaction_coordination(
            store, _stub_crm(), message_service, InMemorySaver()
        )
    },
    repo,
)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _wire_overrides() -> Iterator[None]:
    # This suite drives the demo seed route; conftest enables it before app import.
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_approval_repo] = lambda: repo
    app.dependency_overrides[get_transaction_store] = lambda: store
    app.dependency_overrides[get_transaction_store_admin] = lambda: store
    app.dependency_overrides[get_document_store] = lambda: documents
    app.dependency_overrides[get_message_store] = lambda: message_store
    yield
    app.dependency_overrides.clear()


def test_gate_overdue_milestone_escalates_to_inbox_via_cron() -> None:
    assert client.post("/demo/seed").json()["seeded"] is True

    summary = client.post("/internal/cron/milestones").json()
    assert summary["checked"] == 3
    by_txn = {r["transaction_id"]: r for r in summary["results"]}
    # TXN-1001 has the overdue inspection → paused at the escalation gate
    assert by_txn["TXN-1001"]["status"] == "awaiting_approval"
    # TXN-1002's inspection is due in 2 days → CRM reminders sent, then the
    # drafted reminder email pauses at the outbound-message gate (BOP-019)
    assert by_txn["TXN-1002"]["status"] == "awaiting_approval"
    # TXN-1003 is blocked on the lender → call intent queued
    assert by_txn["TXN-1003"]["outcome"] == "call_queued"

    pending = client.get("/approvals").json()
    assert len(pending) == 2
    approval = next(a for a in pending if a["kind"] == "approve_escalation")
    assert approval["payload"]["transaction_id"] == "TXN-1001"
    assert approval["payload"]["milestones"][0]["days_overdue"] == 2
    message_gate = next(a for a in pending if a["kind"] == "approve_outbound_message")
    assert message_gate["payload"]["transaction_id"] == "TXN-1002"
    assert message_gate["payload"]["recipient"] == "dana.whitfield@example.test"
    assert "Home inspection" in message_gate["payload"]["subject"]

    # a second cron run must NOT stack duplicate gates (escalation or drafted email)
    rerun = client.post("/internal/cron/milestones").json()
    assert rerun["skipped_pending_escalation"] == 2
    assert len(client.get("/approvals").json()) == 2

    # approve → URGENT CRM task + escalation level ratchet
    decided = client.post(
        f"/approvals/{approval['id']}/decide",
        json={"decision": "approved", "decided_by": "tc-lead"},
    ).json()
    assert decided["workflow"]["status"] == "completed"
    assert decided["workflow"]["output"]["outcome"] == "escalated"
    assert len(decided["workflow"]["output"]["escalated_task_ids"]) == 1

    detail = client.get("/transactions/TXN-1001").json()
    inspection = next(m for m in detail["milestones"] if m["id"] == "MS-1001-INS")
    assert inspection["escalation_level"] == 1
    assert inspection["classification"] == "overdue"

    # approve the drafted reminder with an edited body → the edited text is
    # exactly what ships, and the message row records the send
    decided_msg = client.post(
        f"/approvals/{message_gate['id']}/decide",
        json={
            "decision": "approved",
            "decided_by": "tc-lead",
            "edited_payload": {"body": "Edited by the TC — please confirm the inspection."},
        },
    ).json()
    assert decided_msg["workflow"]["status"] == "completed"
    assert decided_msg["workflow"]["output"]["outcome"] == "reminder_email_sent"
    message_id = decided_msg["workflow"]["output"]["reminder_message_id"]

    row = client.get(f"/messages/{message_id}").json()
    assert row["status"] == "sent"
    assert row["recipient"] == "dana.whitfield@example.test"
    assert row["body"] == "Edited by the TC — please confirm the inspection."
    assert email.sent[-1].body == "Edited by the TC — please confirm the inspection."


def test_transactions_list_includes_assessed_milestones() -> None:
    listed = client.get("/transactions").json()
    assert {d["transaction"]["id"] for d in listed} == {"TXN-1001", "TXN-1002", "TXN-1003"}
    txn_3 = next(d for d in listed if d["transaction"]["id"] == "TXN-1003")
    financing = next(m for m in txn_3["milestones"] if m["type"] == "financing")
    assert financing["classification"] == "blocked_external"


def test_seed_is_idempotent_without_reset() -> None:
    assert client.post("/demo/seed").json()["seeded"] is False


def test_cron_secret_enforced_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRON_SECRET", "topsecret")
    assert client.post("/internal/cron/milestones").status_code == 401
    ok = client.post("/internal/cron/milestones", headers={"X-Cron-Key": "topsecret"})
    assert ok.status_code == 200
