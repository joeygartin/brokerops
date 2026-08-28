from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from brokerops_core.models.shadow_parity import (
    ParityVerdict,
    ShadowActualsFile,
    ShadowAllocationLine,
    ShadowDealActual,
    ShadowDealResult,
)
from brokerops_core.services.shadow_parity import (
    FixtureShadowLedgerSource,
    ShadowParityMismatch,
    ShadowSourceNotConfigured,
    UnconfiguredShadowLedgerSource,
    PARITY_PASS_BAR,
    run_shadow_parity,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "shadow_parity_actuals.json"


def load_actuals() -> ShadowActualsFile:
    raw: dict[str, Any] = json.loads(FIXTURE_PATH.read_text())
    raw.pop("_comment", None)
    return ShadowActualsFile.model_validate(raw)


@pytest.mark.asyncio
async def test_fixture_office_matches_and_gate_met() -> None:
    actuals = load_actuals()
    report = await run_shadow_parity(actuals, FixtureShadowLedgerSource())
    assert report.verdict is ParityVerdict.PASS
    assert report.gate_met is True
    assert PARITY_PASS_BAR in report.notes
    assert {r.deal_id for r in report.deals} == {
        "deal-match-80-20",
        "deal-match-fees",
    }
    assert all(r.matched and r.locked and r.replay_identical for r in report.deals)


@pytest.mark.asyncio
async def test_mismatch_fails_loud() -> None:
    actuals = load_actuals()
    deal = actuals.deals[0]
    wrong = ShadowDealResult(
        deal_id=deal.deal_id,
        locked=True,
        allocations=[
            ShadowAllocationLine(
                recipient_type="brokerage",
                recipient_id="brokerage",
                amount_minor=1,
                calc_stage="broker_split",
            ),
            ShadowAllocationLine(
                recipient_type="agent",
                recipient_id="agent-a",
                amount_minor=deal.gci_minor - 1,
                calc_stage="agent_net",
            ),
        ],
    )

    class WrongSource:
        async def ledger_for_deal(self, _: ShadowDealActual) -> ShadowDealResult:
            return wrong

    with pytest.raises(ShadowParityMismatch) as exc:
        await run_shadow_parity(actuals, WrongSource())
    assert exc.value.report.verdict is ParityVerdict.FAIL
    assert exc.value.report.gate_met is False


@pytest.mark.asyncio
async def test_unlocked_snapshot_is_not_a_truthful_pass() -> None:
    actuals = load_actuals()
    deal = actuals.deals[0].model_copy(update={"shadow_locked": False})
    file = ShadowActualsFile(office_id=actuals.office_id, deals=[deal])
    with pytest.raises(ShadowParityMismatch) as exc:
        await run_shadow_parity(file, FixtureShadowLedgerSource())
    kinds = {d.kind for d in exc.value.report.deals[0].diffs}
    assert "unlocked" in kinds
    assert exc.value.report.gate_met is False


@pytest.mark.asyncio
async def test_unconfigured_source_is_blocked_not_pass() -> None:
    actuals = load_actuals()
    with pytest.raises(ShadowSourceNotConfigured, match="refusing a silent pass"):
        await run_shadow_parity(actuals, UnconfiguredShadowLedgerSource())


@pytest.mark.asyncio
async def test_empty_actuals_fail_closed() -> None:
    empty = ShadowActualsFile(office_id="fixture-office", deals=[])
    with pytest.raises(ShadowParityMismatch) as exc:
        await run_shadow_parity(empty, FixtureShadowLedgerSource())
    assert exc.value.report.gate_met is False


@pytest.mark.asyncio
async def test_replay_divergence_fails() -> None:
    actuals = load_actuals()
    deal0 = actuals.deals[0]
    match = ShadowDealResult(
        deal_id=deal0.deal_id,
        locked=True,
        allocations=list(deal0.expected_allocations),
    )
    other = match.model_copy(
        update={
            "allocations": [
                ShadowAllocationLine(
                    recipient_type="brokerage",
                    recipient_id="brokerage",
                    amount_minor=deal0.gci_minor,
                    calc_stage="broker_split",
                )
            ]
        }
    )
    calls = {"n": 0}

    class FlipSource:
        async def ledger_for_deal(self, deal: ShadowDealActual) -> ShadowDealResult:
            calls["n"] += 1
            return match if calls["n"] == 1 else other

    with pytest.raises(ShadowParityMismatch) as exc:
        await run_shadow_parity(
            ShadowActualsFile(office_id="fixture-office", deals=[deal0]),
            FlipSource(),
        )
    assert any(d.kind == "replay" for d in exc.value.report.deals[0].diffs)


@pytest.mark.asyncio
async def test_runner_has_no_write_port() -> None:
    import inspect

    from brokerops_core.services import shadow_parity as mod

    src = inspect.getsource(mod)
    assert "CRMPort" not in src
    assert "EmailPort" not in src
    assert "SMSPort" not in src
    assert "create_contact" not in src
    assert "send(" not in src
