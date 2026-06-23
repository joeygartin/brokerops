"""End-to-end audit-ledger flow through the API and the real LangGraph engine.

Drives start → approve and asserts the approval's CRM writes landed in the trail,
linked to the originating approval and attributed to the approver. The engine reaches
the CRM through RecordingCRM exactly as production does (wired in main.py), so this is
the same seam — the cross-engine equivalent is enforced by the {langgraph, adk} e2e
matrix running scripts/e2e_demo_check.sh.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import InMemoryApprovalRepo, InMemoryAuditLog
from brokerops_api.deps import get_approval_repo, get_audit_log, get_workflow_engine
from brokerops_api.main import app
from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus
from brokerops_core.services.audit import RecordingCRM
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app
from brokerops_langgraph.engine import LangGraphWorkflowEngine
from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract

LISTING = Listing(
    mls_id="RM1001",
    status=ListingStatus.ACTIVE,
    address="412 Alder Court, Rivermouth, CA 95890",
    city="Rivermouth",
    state="CA",
    postal_code="95890",
    list_price=489000,
    bedrooms=3,
    bathrooms=2,
    agent_id="AGT-001",
    agent_name="Dana Whitfield",
    modified_at=datetime(2026, 6, 8, tzinfo=UTC),
)


class FlowFakeMLS:
    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        return [LISTING]

    async def get_listing(self, listing_key: str) -> Listing | None:
        return LISTING if listing_key == "RM1001" else None

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        return []


def _stub_crm() -> FUBCRMAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    fub_client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=fub_client)


audit_log = InMemoryAuditLog()
repo = InMemoryApprovalRepo()
recording_crm = RecordingCRM(_stub_crm(), audit_log)
engine = LangGraphWorkflowEngine(
    {
        "listing_to_contract": build_listing_to_contract(
            FlowFakeMLS(), recording_crm, InMemorySaver()
        )
    },
    repo,
)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _wire_overrides() -> Iterator[None]:
    app.dependency_overrides[get_workflow_engine] = lambda: engine
    app.dependency_overrides[get_approval_repo] = lambda: repo
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    yield
    app.dependency_overrides.clear()


def test_approval_writes_are_recorded_and_linked() -> None:
    started = client.post(
        "/workflows/listing-to-contract/start", json={"listing_key": "RM1001"}
    ).json()
    thread_id = started["thread_id"]
    approval_id = started["approval"]["id"]

    # Nothing has crossed the CRM boundary yet — the run is paused at approval.
    assert client.get(f"/audit?workflow_run_id={thread_id}").json() == []

    decided = client.post(f"/approvals/{approval_id}/decide", json={"decision": "approved"})
    assert decided.status_code == 200
    planned = decided.json()["workflow"]["output"]["planned_tasks"]

    trail = client.get(f"/audit?workflow_run_id={thread_id}").json()
    assert len(trail) == len(planned) > 0
    assert {r["tool"] for r in trail} == {"create_task"}
    for record in trail:
        assert record["integration"] == "followupboss"
        assert record["outcome"] == "success"
        assert record["external_id"]
        # Every write on the resume path links to the approval that authorized it
        # and is attributed to the approver (the demo operator here).
        assert record["approval_id"] == approval_id
        assert record["actor"] == "operator@demo.brokerops"


def test_filter_isolates_runs_and_limit_is_bounded() -> None:
    first = client.post(
        "/workflows/listing-to-contract/start", json={"listing_key": "RM1001"}
    ).json()
    client.post(f"/approvals/{first['approval']['id']}/decide", json={"decision": "approved"})

    # A second, independent run.
    second = client.post(
        "/workflows/listing-to-contract/start", json={"listing_key": "RM1001"}
    ).json()
    client.post(f"/approvals/{second['approval']['id']}/decide", json={"decision": "approved"})

    only_first = client.get(f"/audit?workflow_run_id={first['thread_id']}").json()
    assert {r["workflow_run_id"] for r in only_first} == {first["thread_id"]}

    assert client.get("/audit?limit=1001").status_code == 422


def test_rejection_records_nothing() -> None:
    started = client.post(
        "/workflows/listing-to-contract/start", json={"listing_key": "RM1001"}
    ).json()
    client.post(f"/approvals/{started['approval']['id']}/decide", json={"decision": "rejected"})
    assert client.get(f"/audit?workflow_run_id={started['thread_id']}").json() == []
