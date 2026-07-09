from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from brokerops_api.deps import (
    get_current_principal,
    get_document_store,
    get_transaction_store,
    require_role,
)
from brokerops_core.models.document import Document
from brokerops_core.models.milestone import Milestone, MilestoneStatus
from brokerops_core.models.transaction import Transaction, TransactionParty
from brokerops_core.ports.documents import DocumentStore
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.transactions import TransactionAlreadyExists, TransactionStore
from brokerops_core.services.egress import scrub_payload
from brokerops_core.services.milestone_engine import assess_milestone, expected_document_satisfied
from brokerops_core.services.transaction_open import build_open_transaction

router = APIRouter(prefix="/transactions", tags=["transactions"])

StoreDep = Annotated[TransactionStore, Depends(get_transaction_store)]
DocumentsDep = Annotated[DocumentStore, Depends(get_document_store)]
# Opening a transaction is an action, not a read — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]
# The board and the hub read transactions viewer-open; the response is filtered to
# the caller's role (BOP-027) so a viewer receives party names/roles but not their
# contact email (TransactionParty.email is CONTACT_PII).
ReaderDep = Annotated[Principal, Depends(get_current_principal)]


class MilestoneView(Milestone):
    classification: str
    days_until_due: int
    # BOP-021: whether the expected document (if any) is attached — a read-only
    # report, no workflow routes on it. None when nothing is expected.
    document_satisfied: bool | None = None


class TransactionDetail(BaseModel):
    transaction: Transaction
    milestones: list[MilestoneView]
    documents: list[Document]


def _view(milestone: Milestone, today: date, documents: list[Document]) -> MilestoneView:
    if milestone.status is MilestoneStatus.PENDING:
        assessment = assess_milestone(milestone, today)
        classification = assessment.classification.value
        days = assessment.days_until_due
    else:
        classification = milestone.status.value
        days = (milestone.due_date - today).days
    return MilestoneView(
        **milestone.model_dump(),
        classification=classification,
        days_until_due=days,
        document_satisfied=expected_document_satisfied(milestone, documents),
    )


async def _detail(
    store: TransactionStore, docs: DocumentStore, transaction: Transaction
) -> TransactionDetail:
    today = date.today()
    milestones = await store.list_milestones(transaction.id)
    documents = await docs.list_for_transaction(transaction.id)
    return TransactionDetail(
        transaction=transaction,
        milestones=[_view(m, today, documents) for m in milestones],
        documents=documents,
    )


class OpenTransaction(BaseModel):
    listing_key: str
    contract_date: date
    close_date: date | None = None
    parties: list[TransactionParty] = Field(default_factory=list)


def _same_terms(existing: Transaction, requested: Transaction) -> bool:
    return (
        existing.contract_date == requested.contract_date
        and existing.close_date == requested.close_date
        and existing.parties == requested.parties
    )


async def _replay_or_conflict(
    existing: Transaction,
    requested: Transaction,
    store: TransactionStore,
    docs: DocumentStore,
    response: Response,
) -> TransactionDetail:
    """An existing transaction for this listing: a same-terms repeat is an
    idempotent replay (200); different terms is a conflict (409), never silent."""
    if not _same_terms(existing, requested):
        raise HTTPException(
            status_code=409,
            detail=f"a transaction for listing {existing.listing_key!r} already exists "
            "with different terms",
        )
    response.status_code = 200
    return await _detail(store, docs, existing)


@router.post("", status_code=201)
async def open_transaction(
    body: OpenTransaction,
    store: StoreDep,
    docs: DocumentsDep,
    principal: OperatorDep,
    response: Response,
) -> TransactionDetail:
    """Open an escrow for a listing and generate its milestone timeline.

    Idempotent per listing: the id is derived from the listing key, so a repeat
    with the same terms returns the existing transaction (200) instead of opening
    a second (201); a repeat with different terms is a 409. Invalid escrow dates
    are rejected (422) before anything is persisted. V1 assumes one transaction
    per listing.
    """
    try:
        transaction, milestones = build_open_transaction(
            body.listing_key, body.contract_date, body.close_date, body.parties
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing = await store.get_transaction(transaction.id)
    if existing is not None:
        return await _replay_or_conflict(existing, transaction, store, docs, response)

    try:
        await store.create_transaction(transaction, milestones)
    except TransactionAlreadyExists:
        # Lost a concurrent open for the same listing — return the winner.
        existing = await store.get_transaction(transaction.id)
        if existing is None:  # pragma: no cover - the row must exist after a PK conflict
            raise
        return await _replay_or_conflict(existing, transaction, store, docs, response)
    return await _detail(store, docs, transaction)


@router.get("")
async def list_transactions(
    store: StoreDep, docs: DocumentsDep, principal: ReaderDep
) -> list[TransactionDetail]:
    active = await store.list_active_transactions()
    details = [await _detail(store, docs, transaction) for transaction in active]
    scrubbed: list[TransactionDetail] = scrub_payload(details, recipient_role=principal.role)
    return scrubbed


@router.get("/{transaction_id}")
async def get_transaction(
    transaction_id: str, store: StoreDep, docs: DocumentsDep, principal: ReaderDep
) -> TransactionDetail:
    transaction = await store.get_transaction(transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail=f"transaction {transaction_id!r} not found")
    detail = await _detail(store, docs, transaction)
    scrubbed: TransactionDetail = scrub_payload(detail, recipient_role=principal.role)
    return scrubbed
