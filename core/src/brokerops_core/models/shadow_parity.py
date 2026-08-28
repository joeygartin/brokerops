"""Shadow-parity contracts (BOP-043).

Fixture actuals, an injected shadow ledger snapshot, and the parity report.
No commission math lives here. Amounts are integer minor units.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ShadowAllocationLine(BaseModel):
    """One ledger line as the harness sees it — labels + integer minor units."""

    model_config = ConfigDict(extra="forbid")

    recipient_type: str
    recipient_id: str
    amount_minor: int
    calc_stage: str
    entry_type: str = "original"


class ShadowDealActual(BaseModel):
    """One closed-deal actual from a local fixture (read-only baseline)."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    office_id: str
    close_date: date
    gci_minor: int
    currency: str = "USD"
    expected_allocations: list[ShadowAllocationLine]
    # Optional pre-baked shadow snapshot used only by FixtureShadowLedgerSource.
    shadow_ledger: list[ShadowAllocationLine] | None = None
    shadow_locked: bool = True


class ShadowDealResult(BaseModel):
    """Ledger snapshot returned by an injected shadow source for one deal."""

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
    """On-disk fixture envelope."""

    model_config = ConfigDict(extra="forbid")

    office_id: str
    deals: list[ShadowDealActual]
