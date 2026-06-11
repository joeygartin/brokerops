"""The Phase 4 gate at the API level: cron run → overdue milestone escalates
to the approvals inbox → approval creates an URGENT CRM task and ratchets the
escalation level. Also covers reminder fan-out and pending-escalation dedup."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import InMemoryApprovalRepo, InMemoryTransactionStore
from brokerops_api.deps import (
    get_approval_repo,
    get_transaction_store,
    get_transaction_store_admin,
    get_workflow_engine,
)
from brokerops_api.main import app
from brokerops_api.workflows import TRANSACTION_COORDINATION, WorkflowEngine
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app
from brokerops_langgraph.graphs.transaction_coordination import build_transaction_coordination

fub_stub = create_stub_app()


def _stub_crm() -> FUBCRMAdapter:
    transport = httpx.ASGITransport(app=fub_stub)
    fub_client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=fub_client)


store = InMemoryTransactionStore()
repo = InMemoryApprovalRepo()
engine = WorkflowEngine(
    {TRANSACTION_COORDINATION: build_transaction_coordination(store, _stub_crm(), InMemorySaver())},
    repo,
)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _wire_overrides() -> Iterator[None]:
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_approval_repo] = lambda: repo
    app.dependency_overrides[get_transaction_store] = lambda: store
    app.dependency_overrides[get_transaction_store_admin] = lambda: store
    yield
    app.dependency_overrides.clear()


def test_gate_overdue_milestone_escalates_to_inbox_via_cron() -> None:
    assert client.post("/demo/seed").json()["seeded"] is True

    summary = client.post("/internal/cron/milestones").json()
    assert summary["checked"] == 3
    by_txn = {r["transaction_id"]: r for r in summary["results"]}
    # TXN-1001 has the overdue inspection → paused at the escalation gate
    assert by_txn["TXN-1001"]["status"] == "awaiting_approval"
    # TXN-1002's inspection is due in 2 days → reminders sent, completed
    assert by_txn["TXN-1002"]["outcome"] == "reminders_sent"
    # TXN-1003 is blocked on the lender → call intent queued
    assert by_txn["TXN-1003"]["outcome"] == "call_queued"

    pending = client.get("/approvals").json()
    assert len(pending) == 1
    approval = pending[0]
    assert approval["kind"] == "approve_escalation"
    assert approval["payload"]["transaction_id"] == "TXN-1001"
    assert approval["payload"]["milestones"][0]["days_overdue"] == 2

    # a second cron run must NOT stack a duplicate escalation
    rerun = client.post("/internal/cron/milestones").json()
    assert rerun["skipped_pending_escalation"] == 1
    assert len(client.get("/approvals").json()) == 1

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
