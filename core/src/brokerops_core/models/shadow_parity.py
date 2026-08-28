"""Shadow-parity contracts (BOP-043).

Fixture actuals, a frozen shadow ledger snapshot, and the parity report.
No commission math lives here. Amounts are strict integer minor units.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class ShadowAllocationLine(BaseModel):
    """One ledger line as the harness sees it — labels + integer minor units."""

    model_config = ConfigDict(extra="forbid")

    recipient_type: str
    recipient_id: str
    amount_minor: StrictInt
    calc_stage: str
    entry_type: str = "original"


class ShadowDealActual(BaseModel):
    """One closed-deal actual from a local fixture (read-only baseline)."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    office_id: str
    close_date: date
    gci_minor: StrictInt
    currency: str = "USD"
    expected_allocations: list[ShadowAllocationLine]


class ShadowDealResult(BaseModel):
    """Frozen ledger snapshot for one deal (data only — no I/O)."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    locked: bool
    allocations: list[ShadowAllocationLine]


class DealParityDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    detail: str


class DealParityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    matched: bool
    expected_sum_minor: int
    observed_sum_minor: int
    locked: bool
    replay_identical: bool
    diffs: list[DealParityDiff] = Field(default_factory=list)


class ParityVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


class ParityReport(BaseModel):
    """Per-office gate report. gate_met is True only on a truthful PASS."""

    model_config = ConfigDict(extra="forbid")

    office_id: str
    verdict: ParityVerdict
    gate_met: bool
    deals: list[DealParityRow]
    notes: list[str] = Field(default_factory=list)


class ShadowActualsFile(BaseModel):
    """On-disk actuals envelope. Duplicate deal_id values fail closed."""

    model_config = ConfigDict(extra="forbid")

    office_id: str
    deals: list[ShadowDealActual]

    @model_validator(mode="after")
    def _unique_deal_ids(self) -> ShadowActualsFile:
        ids = [d.deal_id for d in self.deals]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate deal_id values: {dupes}")
        return self


class ShadowSnapshotFile(BaseModel):
    """On-disk independent ledger snapshot. Duplicate deal_id values fail closed."""

    model_config = ConfigDict(extra="forbid")

    office_id: str
    deals: list[ShadowDealResult]

    @model_validator(mode="after")
    def _unique_deal_ids(self) -> ShadowSnapshotFile:
        ids = [d.deal_id for d in self.deals]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate deal_id values: {dupes}")
        return self
