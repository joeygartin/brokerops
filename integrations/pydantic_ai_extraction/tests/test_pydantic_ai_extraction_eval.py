"""Key-gated eval: the PydanticAI adapter against the five real golden calls.

Skipped unless LLM_API_KEY is set (so CI and the zero-credential demo never
hit the API). This is the parity check with the raw-SDK adapter's eval — the
same golden fixtures, the same load-bearing assertions — so the two LLM
backends are held to the same bar:

    LLM_API_KEY=sk-ant-... uv run pytest integrations/pydantic_ai_extraction/tests -q

The golden fixtures' `expected` blocks are the target; assertions cover the
load-bearing fields exactly and treat the free-text lists as coverage checks
(LLM phrasing varies, so exact membership would flake).
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from brokerops_core.services.feedback_extraction import ExtractedFeedback
from brokerops_pydantic_ai_extraction.adapter import DEFAULT_MODEL, PydanticAIExtractionAdapter

pytestmark = pytest.mark.skipif(
    not os.environ.get("LLM_API_KEY"),
    reason="PydanticAI extraction eval needs LLM_API_KEY (live model call)",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_CALLS_PATH = REPO_ROOT / "core" / "tests" / "fixtures" / "showing_feedback_golden_calls.json"


def golden_calls() -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = json.loads(GOLDEN_CALLS_PATH.read_text())["calls"]
    return calls


def _adapter() -> PydanticAIExtractionAdapter:
    return PydanticAIExtractionAdapter(
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
    )


@pytest.mark.parametrize("call", golden_calls(), ids=lambda call: str(call["id"]))
async def test_pydantic_ai_extraction_matches_golden_expectations(call: dict[str, Any]) -> None:
    expected = ExtractedFeedback.model_validate(call["expected"])
    got = await _adapter().extract(call["transcript"])

    # Exact on the load-bearing fields — these are where the deterministic
    # extractor fails on real transcribed speech (ADR-0006).
    assert got.sentiment == expected.sentiment, f"{call['id']}: sentiment"
    assert got.hot_signal == expected.hot_signal, f"{call['id']}: hot_signal"
    if expected.budget_min is not None:
        assert got.budget_min == expected.budget_min, f"{call['id']}: budget_min"
        assert got.budget_max == expected.budget_max, f"{call['id']}: budget_max"
    if expected.price_opinion is not None:
        assert got.price_opinion == expected.price_opinion, f"{call['id']}: price_opinion"

    # Coverage, not exact membership, for the free-text lists.
    if expected.desired_features:
        assert got.desired_features, f"{call['id']}: expected some desired_features"
    if expected.concerns:
        assert got.concerns, f"{call['id']}: expected some concerns"
