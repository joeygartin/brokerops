"""SqlApprovalRepo against in-memory SQLite — portable table behavior."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_api.db import SqlApprovalRepo, metadata
from brokerops_core.models.approval import ApprovalRequest, ApprovalStatus
from brokerops_core.services.tenancy import tenant_scope


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return eng


def _approval(kind: str = "approve_marketing") -> ApprovalRequest:
    return ApprovalRequest(
        id=uuid4().hex,
        workflow="listing_to_contract",
        graph_thread_id=uuid4().hex,
        kind=kind,
        payload={"kind": kind, "draft": {"headline": "Just Listed"}},
        created_at=datetime.now(UTC),
    )


async def test_create_get_roundtrip(engine: AsyncEngine) -> None:
    repo = SqlApprovalRepo(engine)
    approval = _approval()
    await repo.create(approval)
    loaded = await repo.get(approval.id)
    assert loaded is not None
    assert loaded.payload["draft"]["headline"] == "Just Listed"
    assert loaded.status is ApprovalStatus.PENDING


async def test_list_filters_by_status_and_decide_updates(engine: AsyncEngine) -> None:
    repo = SqlApprovalRepo(engine)
    first, second = _approval(), _approval()
    await repo.create(first)
    await repo.create(second)
    assert len(await repo.list(ApprovalStatus.PENDING)) == 2

    # mark_decided is a tenant-scoped, fail-closed mutation (BOP-006): bind a tenant as
    # the real app does at the request boundary. The rows are unstamped (""), which the
    # ownership predicate treats as owned.
    decided_at = datetime.now(UTC)
    with tenant_scope("demo"):
        await repo.mark_decided(first.id, ApprovalStatus.APPROVED, "demo-operator", decided_at)
    pending = await repo.list(ApprovalStatus.PENDING)
    assert [a.id for a in pending] == [second.id]
    decided = await repo.get(first.id)
    assert decided is not None
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "demo-operator"
