from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from pydantic import ValidationError

from brokerops_core.models.shadow_parity import (
    ParityVerdict,
    ShadowActualsFile,
    ShadowAllocationLine,
    ShadowDealActual,
    ShadowDealResult,
)
from brokerops_core.services.shadow_parity import (
    PARITY_PASS_BAR,
    ShadowParityMismatch,
    ShadowSourceNotConfigured,
    run_shadow_parity,
    snapshot_from_fixture,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "shadow_parity_actuals.json"


def load_actuals() -> ShadowActualsFile:
    raw: dict[str, Any] = json.loads(FIXTURE_PATH.read_text())
    raw.pop("_comment", None)
    return ShadowActualsFile.model_validate(raw)


def test_fixture_office_matches_and_gate_met() -> None:
    actuals = load_actuals()
    report = run_shadow_parity(actuals, snapshot_from_fixture(actuals))
    assert report.verdict is ParityVerdict.PASS
    assert report.gate_met is True
    assert PARITY_PASS_BAR in report.notes
    assert {r.deal_id for r in report.deals} == {
        "deal-match-80-20",
        "deal-match-fees",
    }
    assert all(r.matched and r.locked and r.replay_identical for r in report.deals)


def test_mismatch_fails_loud() -> None:
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
    ledgers = {d.deal_id: wrong for d in actuals.deals}
    with pytest.raises(ShadowParityMismatch) as exc:
        run_shadow_parity(actuals, ledgers)
    assert exc.value.report.verdict is ParityVerdict.FAIL
    assert exc.value.report.gate_met is False


def test_unlocked_snapshot_is_not_a_truthful_pass() -> None:
    actuals = load_actuals()
    deal = actuals.deals[0].model_copy(update={"shadow_locked": False})
    file = ShadowActualsFile(office_id=actuals.office_id, deals=[deal])
    with pytest.raises(ShadowParityMismatch) as exc:
        run_shadow_parity(file, snapshot_from_fixture(file))
    kinds = {d.kind for d in exc.value.report.deals[0].diffs}
    assert "unlocked" in kinds
    assert exc.value.report.gate_met is False


def test_unconfigured_source_is_blocked_not_pass() -> None:
    actuals = load_actuals()
    with pytest.raises(ShadowSourceNotConfigured, match="refusing a silent pass"):
        run_shadow_parity(actuals, {})


def test_empty_actuals_fail_closed() -> None:
    empty = ShadowActualsFile(office_id="fixture-office", deals=[])
    with pytest.raises(ShadowParityMismatch) as exc:
        run_shadow_parity(empty, {})
    assert exc.value.report.gate_met is False


def test_replay_divergence_fails() -> None:
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
    file = ShadowActualsFile(office_id="fixture-office", deals=[deal0])
    with pytest.raises(ShadowParityMismatch) as exc:
        run_shadow_parity(file, {deal0.deal_id: match}, replay={deal0.deal_id: other})
    assert any(d.kind == "replay" for d in exc.value.report.deals[0].diffs)


def test_duplicate_deal_ids_fail_closed() -> None:
    actuals = load_actuals()
    deal = actuals.deals[0]
    with pytest.raises(ValidationError, match="duplicate deal_id"):
        ShadowActualsFile(office_id="fixture-office", deals=[deal, deal])


def test_strict_integer_minor_units() -> None:
    with pytest.raises(ValidationError):
        ShadowDealActual.model_validate(
            {
                "deal_id": "d1",
                "office_id": "fixture-office",
                "close_date": "2026-06-15",
                "gci_minor": "1000000",
                "expected_allocations": [],
            }
        )
    with pytest.raises(ValidationError):
        ShadowDealActual.model_validate(
            {
                "deal_id": "d1",
                "office_id": "fixture-office",
                "close_date": "2026-06-15",
                "gci_minor": True,
                "expected_allocations": [],
            }
        )
    with pytest.raises(ValidationError):
        ShadowAllocationLine.model_validate(
            {
                "recipient_type": "agent",
                "recipient_id": "a",
                "amount_minor": 1.5,
                "calc_stage": "agent_net",
            }
        )


def test_runner_accepts_only_frozen_mappings() -> None:
    hints = get_type_hints(run_shadow_parity)
    assert hints["ledgers"].__name__ == "Mapping"
    src = inspect.getsource(run_shadow_parity)
    assert "CRMPort" not in src
    assert "ledger_for_deal" not in src
    assert "create_contact" not in src
