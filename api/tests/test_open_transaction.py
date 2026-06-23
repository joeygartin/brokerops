"""POST /transactions opens an escrow idempotently with a generated timeline.

BOP-004 step 3 + round-2 review fixes (idempotency race, 409 conflict, date
validation). Auth is off under TestClient (demo principal = admin), so the
operator gate passes here; the gate itself is covered in test_rbac.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from brokerops_api.db import InMemoryTransactionStore
from brokerops_api.deps import get_transaction_store
from brokerops_api.main import app
from brokerops_core.models.milestone import Milestone
from brokerops_core.models.transaction import Transaction
from brokerops_core.ports.transactions import TransactionAlreadyExists
from brokerops_core.services.transaction_open import transaction_id_for_listing

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
    assert detail["transaction"]["id"] == transaction_id_for_listing("RM-3001")
    assert detail["transaction"]["stage"] == "under_contract"
    assert len(detail["milestones"]) == 5
    closing = next(m for m in detail["milestones"] if m["type"] == "closing")
    assert closing["due_date"] == "2026-07-01"  # anchored on close_date


def test_open_transaction_idempotent_same_terms() -> None:
    first = client.post("/transactions", json=_body())
    assert first.status_code == 201
    second = client.post("/transactions", json=_body())
    assert second.status_code == 200  # replay returns the existing transaction
    assert second.json()["transaction"]["id"] == first.json()["transaction"]["id"]
    listed = client.get("/transactions").json()
    assert len(listed) == 1  # no duplicate
    assert len(listed[0]["milestones"]) == 5


def test_open_transaction_conflicting_terms_returns_409() -> None:
    assert client.post("/transactions", json=_body()).status_code == 201
    # same listing, different close date — a conflict, not a silent no-op
    conflict = client.post("/transactions", json=_body(close_date="2026-08-01"))
    assert conflict.status_code == 409
    assert client.get("/transactions").json()[0]["transaction"]["close_date"] == "2026-07-01"


def test_open_transaction_loses_race_returns_existing_200() -> None:
    # The pre-check sees nothing, but the insert collides with a rival's write.
    class RacingStore(InMemoryTransactionStore):
        def __init__(self) -> None:
            super().__init__()
            self._rival_won = False

        async def create_transaction(
            self, transaction: Transaction, txn_milestones: list[Milestone]
        ) -> None:
            if not self._rival_won:
                self._rival_won = True
                await super().create_transaction(transaction, txn_milestones)  # rival commits
                raise TransactionAlreadyExists(transaction.id)  # our write loses
            await super().create_transaction(transaction, txn_milestones)

    racing = RacingStore()
    app.dependency_overrides[get_transaction_store] = lambda: racing
    resp = client.post("/transactions", json=_body())
    assert resp.status_code == 200  # returned the winner, not a 500


def test_open_transaction_is_picked_up_by_reads() -> None:
    client.post("/transactions", json=_body(listing_key="RM-3002"))
    detail = client.get(f"/transactions/{transaction_id_for_listing('RM-3002')}")
    assert detail.status_code == 200
    assert detail.json()["transaction"]["listing_key"] == "RM-3002"


def test_open_transaction_close_before_contract_is_422() -> None:
    resp = client.post("/transactions", json=_body(close_date="2026-05-01"))
    assert resp.status_code == 422


def test_open_transaction_without_close_date_is_rejected() -> None:
    # The default timeline anchors closing/walkthrough on the close date.
    resp = client.post("/transactions", json=_body(close_date=None))
    assert resp.status_code == 422


def test_open_transaction_listing_key_too_long_is_422() -> None:
    # Raw listing_key over the String(36) column bound is rejected before persistence.
    resp = client.post("/transactions", json=_body(listing_key="R" * 37))
    assert resp.status_code == 422
