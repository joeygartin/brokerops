"""BOP-043 shadow-parity runner.

Read-only: compares actuals to a frozen ``dict`` of already-copied
``ShadowDealResult`` rows. Custom Mapping types are rejected before any
item access. Missing snapshot rows block the run rather than silently passing.

Pass bar (an office meets the gate iff all of these hold):
  1. Every deal in the actuals set is compared exactly once (unique deal_id).
  2. Each shadow result is locked (committed ledger, not intent).
  3. Allocation lines match actuals exactly (recipient, amount_minor, stage).
  4. Observed lines sum to the deal's gci_minor (reconciliation).
  5. An optional replay dict is identical (or omitted: data is frozen).
  6. The runner performed no client-system writes.
Mismatch raises ShadowParityMismatch; PASS is never emitted on a partial run.
"""

from __future__ import annotations

from brokerops_core.models.shadow_parity import (
    DealParityDiff,
    DealParityRow,
    ParityReport,
    ParityVerdict,
    ShadowAllocationLine,
    ShadowDealActual,
    ShadowDealResult,
    ShadowSnapshotFile,
    ShadowActualsFile,
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
    """No ledger snapshot for a deal — cannot claim a gate pass."""


class ShadowSnapshotNotFrozen(Exception):
    """Ledger input is not an inert dict of validated results."""


def snapshot_dict(file: ShadowSnapshotFile) -> dict[str, ShadowDealResult]:
    """Copy snapshot rows into a plain dict of validated models."""
    return {row.deal_id: ShadowDealResult.model_validate(row.model_dump()) for row in file.deals}


def _freeze_ledgers(ledgers: object) -> dict[str, ShadowDealResult]:
    if type(ledgers) is not dict:
        raise ShadowSnapshotNotFrozen(
            "ledger snapshot must be a plain dict of ShadowDealResult; "
            "custom mappings are rejected before access"
        )
    frozen: dict[str, ShadowDealResult] = {}
    for key, value in list(ledgers.items()):
        if type(key) is not str:
            raise ShadowSnapshotNotFrozen("ledger snapshot keys must be str")
        if not isinstance(value, ShadowDealResult):
            raise ShadowSnapshotNotFrozen("ledger snapshot values must be ShadowDealResult")
        frozen[key] = ShadowDealResult.model_validate(value.model_dump())
    return frozen


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


def run_shadow_parity(
    actuals: ShadowActualsFile,
    ledgers: dict[str, ShadowDealResult],
    *,
    replay: dict[str, ShadowDealResult] | None = None,
    fail_closed: bool = True,
) -> ParityReport:
    """Compare every actual to a frozen plain-dict snapshot (no I/O).

    ``fail_closed=True`` (default) raises ``ShadowParityMismatch`` on FAIL.
    A missing mapping key raises ``ShadowSourceNotConfigured``.
    """
    frozen = _freeze_ledgers(ledgers)
    frozen_replay = None if replay is None else _freeze_ledgers(replay)

    rows: list[DealParityRow] = []
    notes: list[str] = [PARITY_PASS_BAR]

    for deal in actuals.deals:
        if deal.deal_id not in frozen:
            raise ShadowSourceNotConfigured(
                f"no shadow ledger snapshot for deal {deal.deal_id}; refusing a silent pass"
            )
        first = frozen[deal.deal_id]
        row = _diff_deal(deal, first)
        if frozen_replay is not None:
            second = frozen_replay.get(deal.deal_id)
            if second is None or second != first:
                row = row.model_copy(
                    update={
                        "matched": False,
                        "replay_identical": False,
                        "diffs": [
                            *row.diffs,
                            DealParityDiff(
                                kind="replay",
                                detail="replay snapshot differed; refusing a silent pass",
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
