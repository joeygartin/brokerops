from typing import Protocol

from brokerops_core.models.document import Document


class DocumentAlreadyExists(Exception):
    """Raised by `add` when a document with that id already exists.

    Document ids are derived deterministically from (transaction, file), so a
    caller can catch this, re-read, and treat a repeat attach as an idempotent
    replay instead of surfacing a 500 (mirrors `TransactionAlreadyExists`).
    """


class DocumentStore(Protocol):
    """Persistence boundary for transaction-document metadata (pointers only —
    file bytes live in the office file store, reached via `FilesPort`)."""

    async def add(self, document: Document) -> None:
        """Persist a new document row. Raises `DocumentAlreadyExists` on a
        duplicate id (the atomic claim that makes attaching idempotent)."""
        ...

    async def get(self, document_id: str) -> Document | None: ...

    async def list_for_transaction(self, transaction_id: str) -> list[Document]: ...
