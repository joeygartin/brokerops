"""Always-on offline tests: the PydanticAI drafting adapter through TestModel.

No API calls — ALLOW_MODEL_REQUESTS is switched off around every test here, so
an accidental real-model request fails the test instead of billing anyone. The
switch is fixture-scoped (not module-global): the key-gated live eval collects
in the same pytest process, and a leaked False would block its real calls (the
BOP-014 precedent). These tests prove the seam — agent construction, output_type
wiring, the DraftingPort contract, and the trust-boundary overlay; drafting
*quality* is the eval's job.
"""

import pytest
from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from brokerops_core.models.drafting import DraftContext, DraftedMessage
from brokerops_core.models.message import MessageChannel
from brokerops_core.ports.drafting import DraftingPort
from brokerops_pydantic_ai_drafting.adapter import PydanticAIDraftingAdapter


@pytest.fixture(autouse=True)
def _block_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    # monkeypatch restores the prior value after each test.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


def _context() -> DraftContext:
    return DraftContext(
        channel=MessageChannel.EMAIL,
        recipient="buyer@example.com",
        template_ref="showing_followup:v1",
        params={"recipient_name": "Sam", "listing_address": "123 Main St"},
        contact_id="contact-1",
        listing_key="123 Main St",
        transaction_id="txn-1",
    )


# The model's own routing fields are deliberately hostile: the overlay must win.
DRAFT_ARGS = {
    "channel": "sms",
    "recipient": "attacker@evil.example",
    "subject": "Thanks for touring 123 Main St",
    "body": "Hi Sam, it was great to see you at 123 Main St — any thoughts?",
    "template_ref": "made-up-ref",
    "contact_id": "wrong-contact",
    "listing_key": "wrong-listing",
    "transaction_id": "wrong-txn",
}


def test_satisfies_drafting_port() -> None:
    # The annotation is the assertion: mypy strict verifies the adapter
    # structurally satisfies the Protocol.
    adapter: DraftingPort = PydanticAIDraftingAdapter(api_key="test-key")
    assert adapter is not None


async def test_draft_returns_the_validated_schema() -> None:
    adapter = PydanticAIDraftingAdapter(api_key="test-key")
    with adapter._agent.override(model=TestModel(custom_output_args=DRAFT_ARGS)):
        got = await adapter.draft(_context())
    assert isinstance(got, DraftedMessage)
    # The copy comes from the model.
    assert got.subject == DRAFT_ARGS["subject"]
    assert got.body == DRAFT_ARGS["body"]


async def test_routing_fields_are_overlaid_from_the_context_not_the_model() -> None:
    # The trust boundary: even when the model returns a hostile recipient and
    # re-files the draft, every routing/identity field is the context's.
    adapter = PydanticAIDraftingAdapter(api_key="test-key")
    context = _context()
    with adapter._agent.override(model=TestModel(custom_output_args=DRAFT_ARGS)):
        got = await adapter.draft(context)
    assert got.recipient == context.recipient != DRAFT_ARGS["recipient"]
    assert got.channel is context.channel
    assert got.template_ref == context.template_ref
    assert got.contact_id == context.contact_id
    assert got.listing_key == context.listing_key
    assert got.transaction_id == context.transaction_id


async def test_output_type_wiring_alone_yields_valid_schema() -> None:
    # TestModel synthesizes minimal args from the schema itself, so this passes
    # only if the agent's output_type is really DraftedMessage.
    adapter = PydanticAIDraftingAdapter(api_key="test-key")
    with adapter._agent.override(model=TestModel()):
        got = await adapter.draft(_context())
    assert isinstance(got, DraftedMessage)
