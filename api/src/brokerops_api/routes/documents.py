"""Transaction documents — attach / list / open pointers into the office file store.

brokerops stores only metadata (`Document` rows carrying a `FileRef`); the bytes
stay in the file store. Attaching is idempotent per (transaction, file): the
document id is derived from that pair, so a repeat attach replays (200) instead
of duplicating. Uploads go through the app-state FilesPort, which is wrapped in
the recording + idempotency seams (BOP-021), so every external file write lands
in the action ledger. No doc generation, no OCR, no e-signature.
"""

import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from brokerops_api.deps import (
    get_document_store,
    get_files_port,
    get_transaction_store,
    require_role,
)
from brokerops_core.models.document import Document, DocumentKind
from brokerops_core.models.files import FileRef
from brokerops_core.models.transaction import Transaction
from brokerops_core.ports.documents import DocumentAlreadyExists, DocumentStore
from brokerops_core.ports.files import FilesPort
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.transactions import TransactionStore

router = APIRouter(tags=["documents"])

FilesDep = Annotated[FilesPort, Depends(get_files_port)]
DocumentsDep = Annotated[DocumentStore, Depends(get_document_store)]
TransactionsDep = Annotated[TransactionStore, Depends(get_transaction_store)]
# Attaching/uploading a document is an action — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]


def _document_id(transaction_id: str, file_id: str) -> str:
    """Deterministic id per (transaction, file): re-attaching the same file to
    the same transaction is an idempotent replay, not a second row."""
    digest = hashlib.sha256(f"{transaction_id}:{file_id}".encode()).hexdigest()
    return f"DOC-{digest[:28]}"


@router.get("/files")
async def list_files(folder: str, files: FilesDep) -> list[FileRef]:
    """Browse a named office folder (read-only; an unknown folder is empty)."""
    return await files.list(folder)


async def _require_transaction(store: TransactionStore, transaction_id: str) -> Transaction:
    transaction = await store.get_transaction(transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail=f"transaction {transaction_id!r} not found")
    return transaction


async def _validate_milestone(
    store: TransactionStore, transaction_id: str, milestone_id: str | None
) -> None:
    if milestone_id is None:
        return
    milestone = await store.get_milestone(milestone_id)
    if milestone is None or milestone.transaction_id != transaction_id:
        raise HTTPException(
            status_code=422,
            detail=f"milestone {milestone_id!r} does not belong to transaction {transaction_id!r}",
        )


async def _persist(docs: DocumentStore, document: Document) -> tuple[Document, bool]:
    """Add the document; on a duplicate id return the existing row (replay)."""
    try:
        await docs.add(document)
    except DocumentAlreadyExists:
        existing = await docs.get(document.id)
        if existing is None:  # pragma: no cover - the row must exist after a PK conflict
            raise
        return existing, False
    return document, True


class AttachDocument(BaseModel):
    file_id: str
    kind: DocumentKind = DocumentKind.OTHER
    milestone_id: str | None = None
    title: str = ""


@router.post("/transactions/{transaction_id}/documents", status_code=201)
async def attach_document(
    transaction_id: str,
    body: AttachDocument,
    docs: DocumentsDep,
    files: FilesDep,
    transactions: TransactionsDep,
    principal: OperatorDep,
    response: Response,
) -> Document:
    await _require_transaction(transactions, transaction_id)
    await _validate_milestone(transactions, transaction_id, body.milestone_id)
    ref = await files.get(body.file_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"file {body.file_id!r} not found")
    document = Document(
        id=_document_id(transaction_id, body.file_id),
        transaction_id=transaction_id,
        milestone_id=body.milestone_id,
        kind=body.kind,
        title=body.title or ref.name,
        file=ref,
        uploaded_by=principal.email,
        created_at=datetime.now(UTC),
    )
    stored, created = await _persist(docs, document)
    if not created:
        response.status_code = 200
    return stored


class UploadDocument(BaseModel):
    name: str
    content_text: str
    folder: str = ""  # defaults to the transaction's listing-key folder
    kind: DocumentKind = DocumentKind.OTHER
    milestone_id: str | None = None
    title: str = ""


@router.post("/transactions/{transaction_id}/documents/upload", status_code=201)
async def upload_document(
    transaction_id: str,
    body: UploadDocument,
    docs: DocumentsDep,
    files: FilesDep,
    transactions: TransactionsDep,
    principal: OperatorDep,
) -> Document:
    """Store new content in the office file store and attach it in one step.

    The `put` goes through the recording + idempotency seams, so the external
    write is audited; only the resulting pointer is persisted here.
    """
    transaction = await _require_transaction(transactions, transaction_id)
    await _validate_milestone(transactions, transaction_id, body.milestone_id)
    folder = body.folder or transaction.listing_key
    ref = await files.put(body.name, body.content_text.encode("utf-8"), folder)
    document = Document(
        id=f"DOC-{uuid4().hex[:28]}",
        transaction_id=transaction_id,
        milestone_id=body.milestone_id,
        kind=body.kind,
        title=body.title or ref.name,
        file=ref,
        uploaded_by=principal.email,
        created_at=datetime.now(UTC),
    )
    stored, _ = await _persist(docs, document)
    return stored


@router.get("/transactions/{transaction_id}/documents")
async def list_documents(
    transaction_id: str, docs: DocumentsDep, transactions: TransactionsDep
) -> list[Document]:
    await _require_transaction(transactions, transaction_id)
    return await docs.list_for_transaction(transaction_id)
