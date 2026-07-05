"""SqlDocumentStore against in-memory SQLite — portable table behavior (BOP-021)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brokerops_api.db import SqlDocumentStore, metadata
from brokerops_core.models.document import Document, DocumentKind
from brokerops_core.models.files import FileRef
from brokerops_core.ports.documents import DocumentAlreadyExists


@pytest.fixture
async def engine() -> AsyncEngine:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return eng


def _document(doc_id: str, transaction_id: str = "TXN-1") -> Document:
    return Document(
        id=doc_id,
        transaction_id=transaction_id,
        milestone_id="MS-1",
        kind=DocumentKind.PURCHASE_AGREEMENT,
        title="Purchase agreement",
        file=FileRef(
            file_id="drive-0001",
            name="Purchase agreement.pdf",
            mime_type="application/pdf",
            size_bytes=42,
            web_url="http://localhost:8004/view/drive-0001",
        ),
        uploaded_by="op@example.com",
        created_at=datetime.now(UTC),
    )


async def test_add_get_roundtrip_preserves_the_pointer(engine: AsyncEngine) -> None:
    store = SqlDocumentStore(engine)
    document = _document("DOC-1")
    await store.add(document)
    loaded = await store.get("DOC-1")
    assert loaded is not None
    assert loaded.file == document.file  # the FileRef pointer survives the JSON column
    assert loaded.kind is DocumentKind.PURCHASE_AGREEMENT
    assert loaded.milestone_id == "MS-1"
    assert await store.get("DOC-none") is None


async def test_duplicate_id_raises_already_exists(engine: AsyncEngine) -> None:
    store = SqlDocumentStore(engine)
    await store.add(_document("DOC-1"))
    with pytest.raises(DocumentAlreadyExists):
        await store.add(_document("DOC-1"))


async def test_list_for_transaction_filters_and_orders(engine: AsyncEngine) -> None:
    store = SqlDocumentStore(engine)
    await store.add(_document("DOC-1", "TXN-1"))
    await store.add(_document("DOC-2", "TXN-1"))
    await store.add(_document("DOC-3", "TXN-2"))
    rows = await store.list_for_transaction("TXN-1")
    assert [d.id for d in rows] == ["DOC-1", "DOC-2"]
