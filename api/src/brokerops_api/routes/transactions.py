from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brokerops_api.deps import get_transaction_store
from brokerops_core.models.milestone import Milestone, MilestoneStatus
from brokerops_core.models.transaction import Transaction
from brokerops_core.ports.transactions import TransactionStore
from brokerops_core.services.milestone_engine import assess_milestone

router = APIRouter(prefix="/transactions", tags=["transactions"])

StoreDep = Annotated[TransactionStore, Depends(get_transaction_store)]


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
