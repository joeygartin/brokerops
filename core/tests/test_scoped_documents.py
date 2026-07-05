"""Tenant isolation coverage for ScopedDocumentStore (BOP-021, the BOP-006 pattern).

Inline in-test fakes keep this in core (core must not import the api package).
Proves: a write stamps the bound tenant; a write carrying a foreign tenant id is
denied + audited as a security event; a by-id read never returns another tenant's
row; lists filter; access with no tenant bound fails closed.
"""

from datetime import UTC, datetime

import pytest

from brokerops_core.models.document import Document, DocumentKind
from brokerops_core.models.files import FileRef
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.services.scoped_stores import ScopedDocumentStore
from brokerops_core.services.tenancy import (
    CrossTenantError,
    TenantContextMissing,
    tenant_scope,
)


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


class FakeDocumentStore:
    def __init__(self) -> None:
        self.documents: dict[str, Document] = {}

    async def add(self, document: Document) -> None:
        self.documents[document.id] = document

    async def get(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)

    async def list_for_transaction(self, transaction_id: str) -> list[Document]:
        return [d for d in self.documents.values() if d.transaction_id == transaction_id]


def _document(doc_id: str, *, tenant_id: str = "", transaction_id: str = "tx1") -> Document:
    return Document(
        tenant_id=tenant_id,
        id=doc_id,
        transaction_id=transaction_id,
        kind=DocumentKind.DISCLOSURES,
        title="Seller disclosures",
        file=FileRef(file_id="f1", name="Seller disclosures.pdf"),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_add_stamps_ambient_tenant() -> None:
    inner = FakeDocumentStore()
    store = ScopedDocumentStore(inner, CollectingAuditLog())
    with tenant_scope("demo"):
        await store.add(_document("d1"))
    assert inner.documents["d1"].tenant_id == "demo"


@pytest.mark.asyncio
async def test_add_with_foreign_tenant_is_denied_and_audited() -> None:
    inner = FakeDocumentStore()
    audit = CollectingAuditLog()
    store = ScopedDocumentStore(inner, audit)
    # A prompt-injected node populated tenant_id with another brokerage's id.
    with tenant_scope("demo"):
        with pytest.raises(CrossTenantError):
            await store.add(_document("d1", tenant_id="other-brokerage"))
    assert inner.documents == {}  # nothing written
    assert len(audit.records) == 1
    assert audit.records[0].integration == "security"
    assert audit.records[0].args == {
        "attempted_tenant": "other-brokerage",
        "bound_tenant": "demo",
    }


@pytest.mark.asyncio
async def test_add_rejects_overwrite_of_foreign_row() -> None:
    # Reuse-a-foreign-id: the model claims the bound tenant, but the existing
    # row with that id belongs to another tenant.
    inner = FakeDocumentStore()
    audit = CollectingAuditLog()
    inner.documents["d-a"] = _document("d-a", tenant_id="brokerage-a")
    store = ScopedDocumentStore(inner, audit)
    with tenant_scope("brokerage-b"):
        with pytest.raises(CrossTenantError):
            await store.add(_document("d-a"))
    assert inner.documents["d-a"].tenant_id == "brokerage-a"  # not overwritten
    assert any(r.tool == "add_document" for r in audit.records)


@pytest.mark.asyncio
async def test_get_hides_another_tenants_row_and_audits() -> None:
    inner = FakeDocumentStore()
    audit = CollectingAuditLog()
    inner.documents["d-a"] = _document("d-a", tenant_id="brokerage-a")
    store = ScopedDocumentStore(inner, audit)
    with tenant_scope("brokerage-b"):
        assert await store.get("d-a") is None
    assert len(audit.records) == 1
    assert audit.records[0].tool == "get_document"
    assert audit.records[0].integration == "security"


@pytest.mark.asyncio
async def test_list_filters_to_bound_tenant() -> None:
    inner = FakeDocumentStore()
    inner.documents["a"] = _document("a", tenant_id="brokerage-a")
    inner.documents["b"] = _document("b", tenant_id="brokerage-b")
    store = ScopedDocumentStore(inner, CollectingAuditLog())
    with tenant_scope("brokerage-b"):
        rows = await store.list_for_transaction("tx1")
    assert {d.id for d in rows} == {"b"}


@pytest.mark.asyncio
async def test_access_without_tenant_scope_fails_closed() -> None:
    store = ScopedDocumentStore(FakeDocumentStore(), CollectingAuditLog())
    with pytest.raises(TenantContextMissing):
        await store.get("anything")
    with pytest.raises(TenantContextMissing):
        await store.list_for_transaction("tx1")
