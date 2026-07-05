from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from brokerops_core.models.sensitivity import CONTACT_PII


class TransactionStage(StrEnum):
    UNDER_CONTRACT = "under_contract"
    CONTINGENCIES = "contingencies"
    CLEAR_TO_CLOSE = "clear_to_close"
    CLOSED = "closed"
    CANCELLED = "cancelled"


ACTIVE_STAGES = (
    TransactionStage.UNDER_CONTRACT,
    TransactionStage.CONTINGENCIES,
    TransactionStage.CLEAR_TO_CLOSE,
)


class TransactionParty(BaseModel):
    role: str
    name: str
    contact_id: str | None = None
    # Direct reach for parties that aren't CRM contacts (escrow officers,
    # lenders, co-op agents). Empty = no email on file; the drafted-comms rules
    # (BOP-019) skip such parties rather than guessing an address. Role-restricted
    # PII at the egress boundary (BOP-012).
    email: Annotated[str, CONTACT_PII] = ""


class Transaction(BaseModel):
    # Stamped by the scoped data layer (BOP-006); "" means "use the ambient bound
    # tenant". A node may populate it, but a value disagreeing with the bound tenant
    # is rejected as a cross-tenant attempt before any write.
    tenant_id: str = ""
    id: str
    listing_key: str
    stage: TransactionStage
    parties: list[TransactionParty] = Field(default_factory=list)
    contract_date: date
    close_date: date | None = None

    @property
    def is_active(self) -> bool:
        return self.stage in ACTIVE_STAGES
