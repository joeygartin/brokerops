"""POST /transactions opens an escrow with a generated timeline, idempotently.

BOP-004 step 3. Auth is off under TestClient (demo principal = admin), so the
operator gate passes here; the gate itself is covered in test_rbac.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from brokerops_api.db import InMemoryTransactionStore
from brokerops_api.deps import get_transaction_store
from brokerops_api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def store() -> Iterator[InMemoryTransactionStore]:
    fresh = InMemoryTransactionStore()
    app.dependency_overrides[get_transaction_store] = lambda: fresh
    yield fresh
    app.dependency_overrides.clear()


def _body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "listing_key": "RM-3001",
        "contract_date": "2026-06-01",
        "close_date": "2026-07-01",
    }
    return {**base, **overrides}


def test_open_transaction_generates_timeline() -> None:
    resp = client.post("/transactions", json=_body())
    assert resp.status_code == 201
    detail = resp.json()
    assert detail["transaction"]["id"] == "TXN-RM-3001"
    assert detail["transaction"]["stage"] == "under_contract"
    assert len(detail["milestones"]) == 5
    closing = next(m for m in detail["milestones"] if m["type"] == "closing")
    assert closing["due_date"] == "2026-07-01"  # anchored on close_date


def test_open_transaction_is_idempotent_on_retry() -> None:
    first = client.post("/transactions", json=_body())
    assert first.status_code == 201
    second = client.post("/transactions", json=_body())
    assert second.status_code == 200  # replay returns the existing transaction
    assert second.json()["transaction"]["id"] == first.json()["transaction"]["id"]
    # exactly one transaction, no duplicated milestones
    listed = client.get("/transactions").json()
    assert len(listed) == 1
    assert len(listed[0]["milestones"]) == 5


def test_open_transaction_is_picked_up_by_reads() -> None:
    client.post("/transactions", json=_body(listing_key="RM-3002"))
    detail = client.get("/transactions/TXN-RM-3002")
    assert detail.status_code == 200
    assert detail.json()["transaction"]["listing_key"] == "RM-3002"


def test_open_transaction_without_close_date_is_rejected() -> None:
    # The default timeline anchors closing/walkthrough on the close date.
    resp = client.post("/transactions", json=_body(close_date=None))
    assert resp.status_code == 422
