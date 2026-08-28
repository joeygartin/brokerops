"""BOP-043 shadow-parity runner.

Read-only: never accepts a CRM/email/SMS/write port and never mutates client
systems. Diffs closed-deal actuals against an injected ``ShadowLedgerSource``.
When no source is wired, the run is blocked — not a silent pass.

Pass bar (an office meets the gate iff all of these hold):
  1. Every deal in the actuals set is compared.
  2. Each shadow result is locked (committed ledger, not intent).
  3. Allocation lines match actuals exactly (recipient, amount_minor, stage).
  4. Observed lines sum to the deal's gci_minor (reconciliation).
  5. A second fetch for the same deal_id is identical (replay-safe).
  6. The runner performed no client-system writes.
Mismatch raises ShadowParityMismatch; PASS is never emitted on a partial run.
"""

from __future__ import annotations

from typing import Protocol

from brokerops_core.models.shadow_parity import (
    DealParityDiff,
    DealParityRow,
    ParityReport,
    ParityVerdict,
    ShadowActualsFile,
    ShadowAllocationLine,
    ShadowDealActual,
    ShadowDealResult,
)

PARITY_PASS_BAR = (
    "Parity gate met for an office only when every deal has a locked "
    "shadow ledger whose lines match actuals, sum to GCI, and replay identically; "
    "the parity signal is never emitted on intent, an unlocked snapshot, or a "
    "partial run."
)


class ShadowParityMismatch(Exception):
    """Loud fail-closed: at least one deal did not match."""

    def __init__(self, report: ParityReport) -> None:
        self.report = report
        super().__init__(
            f"shadow-parity FAIL office={report.office_id} "
            f"mismatched={[r.deal_id for r in report.deals if not r.matched]}"
        )


class ShadowSourceNotConfigured(Exception):
    """No injectable shadow ledger source — cannot claim a gate pass."""


class ShadowLedgerSource(Protocol):
    """Injected snapshot provider. Implementations must not write client systems."""

    async def ledger_for_deal(self, deal: ShadowDealActual) -> ShadowDealResult: ...


class UnconfiguredShadowLedgerSource:
    async def ledger_for_deal(self, deal: ShadowDealActual) -> ShadowDealResult:
        raise ShadowSourceNotConfigured("no shadow ledger source is wired; refusing a silent pass")


class FixtureShadowLedgerSource:
    """Uses each deal's optional shadow_ledger field — local fixtures only."""

    async def ledger_for_deal(self, deal: ShadowDealActual) -> ShadowDealResult:
        if deal.shadow_ledger is None:
            raise ShadowSourceNotConfigured(f"deal {deal.deal_id} has no fixture shadow_ledger")
        return ShadowDealResult(
            deal_id=deal.deal_id,
            locked=deal.shadow_locked,
            allocations=list(deal.shadow_ledger),
        )


def _line_key(line: ShadowAllocationLine) -> tuple[str, str, int, str, str]:
    return (
        line.recipient_type,
        line.recipient_id,
        line.amount_minor,
        line.calc_stage,
        line.entry_type,
    )


def _diff_deal(actual: ShadowDealActual, observed: ShadowDealResult) -> DealParityRow:
    diffs: list[DealParityDiff] = []
    expected_sum = sum(a.amount_minor for a in actual.expected_allocations)
    observed_sum = sum(a.amount_minor for a in observed.allocations)

    if observed.deal_id != actual.deal_id:
        diffs.append(
            DealParityDiff(
                kind="deal_id",
                detail=f"observed deal_id {observed.deal_id!r} != {actual.deal_id!r}",
            )
        )
    if not observed.locked:
        diffs.append(
            DealParityDiff(
                kind="unlocked",
                detail="shadow ledger is not locked; parity signal would not be truthful",
            )
        )
    if observed_sum != actual.gci_minor:
        diffs.append(
            DealParityDiff(
                kind="reconciliation",
                detail=f"observed sum {observed_sum} != gci_minor {actual.gci_minor}",
            )
        )
    if expected_sum != actual.gci_minor:
        diffs.append(
            DealParityDiff(
                kind="actuals_gci",
                detail=f"expected sum {expected_sum} != gci_minor {actual.gci_minor}",
            )
        )

    exp = sorted(_line_key(a) for a in actual.expected_allocations)
    obs = sorted(_line_key(a) for a in observed.allocations)
    if exp != obs:
        diffs.append(
            DealParityDiff(
                kind="allocations",
                detail=f"expected {exp} != observed {obs}",
            )
        )

    return DealParityRow(
        deal_id=actual.deal_id,
        matched=not diffs,
        expected_sum_minor=expected_sum,
        observed_sum_minor=observed_sum,
        locked=observed.locked,
        replay_identical=True,
        diffs=diffs,
    )


async def run_shadow_parity(
    actuals: ShadowActualsFile,
    source: ShadowLedgerSource,
    *,
    fail_closed: bool = True,
) -> ParityReport:
    """Compare every fixture deal to the injected shadow source.

    ``fail_closed=True`` (default) raises ``ShadowParityMismatch`` on FAIL.
    BLOCKED (unconfigured source) always raises ``ShadowSourceNotConfigured``.
    """
    rows: list[DealParityRow] = []
    notes: list[str] = [PARITY_PASS_BAR]

    for deal in actuals.deals:
        first = await source.ledger_for_deal(deal)
        second = await source.ledger_for_deal(deal)
        row = _diff_deal(deal, first)
        if first != second:
            row = row.model_copy(
                update={
                    "matched": False,
                    "replay_identical": False,
                    "diffs": [
                        *row.diffs,
                        DealParityDiff(
                            kind="replay",
                            detail="second fetch differed; would imply a non-idempotent write",
                        ),
                    ],
                }
            )
        rows.append(row)

    all_matched = bool(rows) and all(r.matched for r in rows)
    if not rows:
        notes.append("empty actuals file — not a gate pass")
        report = ParityReport(
            office_id=actuals.office_id,
            verdict=ParityVerdict.FAIL,
            gate_met=False,
            deals=rows,
            notes=notes,
        )
    elif all_matched:
        report = ParityReport(
            office_id=actuals.office_id,
            verdict=ParityVerdict.PASS,
            gate_met=True,
            deals=rows,
            notes=notes,
        )
    else:
        report = ParityReport(
            office_id=actuals.office_id,
            verdict=ParityVerdict.FAIL,
            gate_met=False,
            deals=rows,
            notes=notes,
        )

    if fail_closed and report.verdict is not ParityVerdict.PASS:
        raise ShadowParityMismatch(report)
    return report
