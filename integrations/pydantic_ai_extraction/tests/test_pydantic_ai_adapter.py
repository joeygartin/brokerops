"""Always-on offline tests: the PydanticAI adapter through TestModel.

No API calls — ALLOW_MODEL_REQUESTS is switched off around every test here, so
an accidental real-model request fails the test instead of billing anyone. The
switch is fixture-scoped (not module-global): the key-gated live eval collects
in the same pytest process, and a leaked False would block its real calls.
These tests prove the seam (agent construction, output_type wiring, the
ExtractionPort contract); extraction *quality* is the eval's job.
"""

from typing import Any

import pytest
from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from brokerops_core.ports.extraction import ExtractionPort
from brokerops_core.services.feedback_extraction import ExtractedFeedback
from brokerops_pydantic_ai_extraction.adapter import PydanticAIExtractionAdapter


@pytest.fixture(autouse=True)
def _block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    # monkeypatch restores the prior value after each test.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


FEEDBACK_ARGS: dict[str, Any] = {
    "sentiment": "positive",
    "hot_signal": True,
    "highlights": ["kitchen"],
    "concerns": ["small backyard"],
    "price_opinion": "fair",
    "budget_min": 550_000,
    "budget_max": 600_000,
    "desired_features": ["bigger backyard"],
    "summary": "Buyer loved the kitchen and wants to write an offer.",
}


def test_satisfies_extraction_port() -> None:
    # The annotation is the assertion: mypy strict verifies the adapter
    # structurally satisfies the Protocol.
    adapter: ExtractionPort = PydanticAIExtractionAdapter(api_key="test-key")
    assert adapter is not None


async def test_extract_returns_the_validated_schema() -> None:
    adapter = PydanticAIExtractionAdapter(api_key="test-key")
    with adapter._agent.override(model=TestModel(custom_output_args=FEEDBACK_ARGS)):
        got = await adapter.extract("buyer feedback transcript")
    assert isinstance(got, ExtractedFeedback)
    assert got == ExtractedFeedback.model_validate(FEEDBACK_ARGS)


async def test_output_type_wiring_alone_yields_valid_schema() -> None:
    # TestModel synthesizes minimal args from the schema itself, so this
    # passes only if the agent's output_type is really ExtractedFeedback.
    adapter = PydanticAIExtractionAdapter(api_key="test-key")
    with adapter._agent.override(model=TestModel()):
        got = await adapter.extract("buyer feedback transcript")
    assert isinstance(got, ExtractedFeedback)
