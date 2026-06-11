"""Route-level HITL flow: start → pending approval → decide → workflow completes."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import InMemoryApprovalRepo
from brokerops_api.deps import get_approval_repo, get_workflow_engine
from brokerops_api.main import app
from brokerops_api.workflows import WorkflowEngine
from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus
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


repo = InMemoryApprovalRepo()
engine = WorkflowEngine(build_listing_to_contract(FlowFakeMLS(), InMemorySaver()), repo)
app.dependency_overrides[get_workflow_engine] = lambda: engine
app.dependency_overrides[get_approval_repo] = lambda: repo
client = TestClient(app)


def test_full_hitl_round_trip_through_the_api() -> None:
    started = client.post("/workflows/listing-to-contract/start", json={"listing_key": "RM1001"})
    assert started.status_code == 202
    body = started.json()
    assert body["status"] == "awaiting_approval"
    approval_id = body["approval"]["id"]
    assert body["approval"]["kind"] == "approve_marketing"

    pending = client.get("/approvals").json()
    assert approval_id in {a["id"] for a in pending}

    decided = client.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "approved", "decided_by": "demo-operator"},
    )
    assert decided.status_code == 200
    outcome = decided.json()
    assert outcome["approval"]["status"] == "approved"
    assert outcome["approval"]["decided_by"] == "demo-operator"
    assert outcome["workflow"]["status"] == "completed"

    assert client.get("/approvals").json() == []


def test_deciding_twice_conflicts() -> None:
    started = client.post(
        "/workflows/listing-to-contract/start", json={"listing_key": "RM1001"}
    ).json()
    approval_id = started["approval"]["id"]
    decision = {"decision": "rejected", "decided_by": "demo-operator"}
    assert client.post(f"/approvals/{approval_id}/decide", json=decision).status_code == 200
    assert client.post(f"/approvals/{approval_id}/decide", json=decision).status_code == 409


def test_unknown_listing_completes_without_approval() -> None:
    started = client.post(
        "/workflows/listing-to-contract/start", json={"listing_key": "RM9999"}
    ).json()
    assert started["status"] == "not_found"
    assert started["approval"] is None


def test_decide_unknown_approval_404() -> None:
    response = client.post(
        "/approvals/nope/decide", json={"decision": "approved", "decided_by": "x"}
    )
    assert response.status_code == 404
