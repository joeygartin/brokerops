from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from brokerops_api.deps import get_transaction_store, require_role
from brokerops_core.models.milestone import Milestone, MilestoneStatus
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.services.milestone_engine import assess_milestone
from brokerops_core.services.milestone_schedule import generate_milestones

router = APIRouter(prefix="/transactions", tags=["transactions"])

StoreDep = Annotated[TransactionStore, Depends(get_transaction_store)]
# Opening a transaction is an action, not a read — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]


class MilestoneView(Milestone):
    classification: str
    days_until_due: int


class TransactionDetail(BaseModel):
    transaction: Transaction
    milestones: list[MilestoneView]


def _view(milestone: Milestone, today: date) -> MilestoneView:
    if milestone.status is MilestoneStatus.PENDING:
        assessment = assess_milestone(milestone, today)
        classification = assessment.classification.value
        days = assessment.days_until_due
    else:
        classification = milestone.status.value
        days = (milestone.due_date - today).days
    return MilestoneView(
        **milestone.model_dump(), classification=classification, days_until_due=days
    )


async def _detail(store: TransactionStore, transaction: Transaction) -> TransactionDetail:
    today = date.today()
    milestones = await store.list_milestones(transaction.id)
    return TransactionDetail(
        transaction=transaction, milestones=[_view(m, today) for m in milestones]
    )


class OpenTransaction(BaseModel):
    listing_key: str
    contract_date: date
    close_date: date | None = None
    parties: list[TransactionParty] = Field(default_factory=list)


@router.post("", status_code=201)
async def open_transaction(
    body: OpenTransaction, store: StoreDep, principal: OperatorDep, response: Response
) -> TransactionDetail:
    """Open an escrow for a listing and generate its milestone timeline.

    Idempotent: the transaction id is derived from the listing key, so a retry
    finds the existing transaction and returns it (200) rather than opening a
    second one (201). V1 assumes one transaction per listing.
    """
    transaction_id = f"TXN-{body.listing_key}"
    existing = await store.get_transaction(transaction_id)
    if existing is not None:
        response.status_code = 200
        return await _detail(store, existing)

    transaction = Transaction(
        id=transaction_id,
        listing_key=body.listing_key,
        stage=TransactionStage.UNDER_CONTRACT,
        parties=body.parties,
        contract_date=body.contract_date,
        close_date=body.close_date,
    )
    try:
        milestones = generate_milestones(transaction)
    except ValueError as exc:
        # e.g. the timeline anchors a milestone before close but no close_date given.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await store.create_transaction(transaction, milestones)
    return await _detail(store, transaction)


@router.get("")
async def list_transactions(store: StoreDep) -> list[TransactionDetail]:
    active = await store.list_active_transactions()
    return [await _detail(store, transaction) for transaction in active]


@router.get("/{transaction_id}")
async def get_transaction(transaction_id: str, store: StoreDep) -> TransactionDetail:
    transaction = await store.get_transaction(transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail=f"transaction {transaction_id!r} not found")
    return await _detail(store, transaction)
