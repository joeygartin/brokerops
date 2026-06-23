"""Demo controls — seed the demo dataset (synthetic only, idempotent)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from brokerops_api.demo_seed import demo_transactions
from brokerops_api.deps import get_transaction_store_admin
from brokerops_api.db import TransactionStoreAdmin

router = APIRouter(prefix="/demo", tags=["demo"])

AdminDep = Annotated[TransactionStoreAdmin, Depends(get_transaction_store_admin)]


class SeedRequest(BaseModel):
    reset: bool = False


class SeedResult(BaseModel):
    seeded: bool
    transactions: int
    milestones: int


@router.post("/seed")
async def seed_demo_data(admin: AdminDep, body: SeedRequest | None = None) -> SeedResult:
    reset = bool(body and body.reset)
    existing = await admin.count_transactions()
    if existing and not reset:
        return SeedResult(seeded=False, transactions=existing, milestones=0)
    if reset:
        await admin.clear()
    seeded = demo_transactions(date.today())
    milestone_count = 0
    for transaction, milestones in seeded:
        await admin.create_transaction(transaction, milestones)
        milestone_count += len(milestones)
    return SeedResult(seeded=True, transactions=len(seeded), milestones=milestone_count)
