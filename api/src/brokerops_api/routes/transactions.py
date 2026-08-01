import logging
from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from brokerops_api.deps import (
    get_document_store,
    get_listing_service,
    get_transaction_store,
    require_role,
)
from brokerops_api.routes._egress import ScrubDep
from brokerops_core.models.document import Document
from brokerops_core.models.milestone import Milestone, MilestoneStatus
from brokerops_core.models.transaction import Transaction, TransactionParty
from brokerops_core.ports.documents import DocumentStore
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.transactions import TransactionAlreadyExists, TransactionStore
from brokerops_core.services.listing_service import ListingService
from brokerops_core.services.milestone_engine import (
    DeadlineItem,
    assess_milestone,
    build_deadline_queue,
    expected_document_satisfied,
)
from brokerops_core.services.transaction_open import build_open_transaction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transactions", tags=["transactions"])

StoreDep = Annotated[TransactionStore, Depends(get_transaction_store)]
DocumentsDep = Annotated[DocumentStore, Depends(get_document_store)]
ListingsDep = Annotated[ListingService, Depends(get_listing_service)]
# Opening a transaction is an action, not a read — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]
# The board and the hub read transactions viewer-open; the response is caller-role
# filtered via the shared egress seam (BOP-040/ScrubDep) so a viewer receives party
# names/roles but not their contact email (TransactionParty.email is CONTACT_PII).


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
    store: StoreDep, docs: DocumentsDep, scrub: ScrubDep
) -> list[TransactionDetail]:
    active = await store.list_active_transactions()
    details = [await _detail(store, docs, transaction) for transaction in active]
    return scrub(details)


class DeadlineRow(DeadlineItem):
    # The core queue item plus the transaction context a coordinator needs to
    # recognise the deal at a glance. `listing_key` comes free off the
    # transaction; the row links back to the hub via `transaction_id` (BOP-027).
    listing_key: str


@router.get("/deadlines")
async def deadline_queue(store: StoreDep, scrub: ScrubDep) -> list[DeadlineRow]:
    """The coordinator's cross-transaction deadline queue (BOP-030).

    Every active transaction's pending milestones, classified due-soon / overdue /
    blocked-external and sorted most-urgent-first. The classification and ordering
    rules live in the core milestone engine; this route only enumerates and joins
    the listing key for display.
    """
    today = date.today()
    active = await store.list_active_transactions()
    listing_key_by_txn = {transaction.id: transaction.listing_key for transaction in active}
    milestones: list[Milestone] = []
    for transaction in active:
        milestones.extend(await store.list_milestones(transaction.id))
    rows = [
        DeadlineRow(
            **item.model_dump(),
            listing_key=listing_key_by_txn.get(item.transaction_id, ""),
        )
        for item in build_deadline_queue(milestones, today)
    ]
    # No PII on a deadline row, so this is a no-op today — kept for parity with the
    # other hub reads so a future field carrying PII is filtered by default.
    return scrub(rows)


class TransactionSearchRow(BaseModel):
    transaction: Transaction
    # Joined from the listing (transactions store only the key); "" when the
    # listing has no address on file or the feed can't serve it.
    property_address: str


async def _listing_address(listings: ListingService, listing_key: str) -> str:
    """Best-effort property address for a transaction's listing.

    Address is an enrichment on a search result, never the backbone of the match
    (listing key + party name carry that), so a listing the MLS can't serve — a
    closed key, a sparse or unreachable feed — degrades to no address rather than
    failing the whole search.

    The degradation is scoped to *expected* feed failures (`httpx.HTTPError`: a 4xx
    for an unknown key, a network/connection error for a down feed) and is logged at
    WARNING so a real MLS-integration regression stays visible instead of being
    silently swallowed. Any other exception propagates — a genuine bug should fail
    loud, not hide behind a blank address.
    """
    try:
        listing = await listings.get_with_media(listing_key)
    except httpx.HTTPError as exc:
        logger.warning(
            "search: MLS listing lookup failed for %r, dropping address enrichment: %s",
            listing_key,
            exc,
        )
        return ""
    return listing.address if listing is not None else ""


@router.get("/search")
async def search_transactions(
    q: str,
    store: StoreDep,
    listings: ListingsDep,
    scrub: ScrubDep,
) -> list[TransactionSearchRow]:
    """Find active transactions by listing key, party (contact) name, or property
    address (BOP-030 viewer home).

    A thin case-insensitive substring match over the active transactions — the same
    working set the board and the deadline queue read. A blank query returns
    nothing. The response is filtered to the caller's role (BOP-027), so a viewer
    sees party names but not their contact emails.
    """
    needle = q.strip().lower()
    if not needle:
        return []
    rows: list[TransactionSearchRow] = []
    for transaction in await store.list_active_transactions():
        address = await _listing_address(listings, transaction.listing_key)
        haystack = " ".join(
            [transaction.listing_key, address, *(party.name for party in transaction.parties)]
        ).lower()
        if needle in haystack:
            rows.append(TransactionSearchRow(transaction=transaction, property_address=address))
    return scrub(rows)


@router.get("/{transaction_id}")
async def get_transaction(
    transaction_id: str, store: StoreDep, docs: DocumentsDep, scrub: ScrubDep
) -> TransactionDetail:
    transaction = await store.get_transaction(transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail=f"transaction {transaction_id!r} not found")
    detail = await _detail(store, docs, transaction)
    return scrub(detail)
