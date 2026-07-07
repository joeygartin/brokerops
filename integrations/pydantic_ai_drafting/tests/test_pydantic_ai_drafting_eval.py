"""Key-gated eval: the PydanticAI drafting adapter against a real model.

Skipped unless LLM_API_KEY is set (so CI and the zero-credential demo never hit
the API). Not a CI guard — LLM copy is non-deterministic — but a live sanity
check that a real model, given a grounded DraftContext, produces a sendable
message and honors the two load-bearing properties: the routing fields are the
context's (the trust-boundary overlay), and the copy stays grounded (it does not
leak the internal template ref or a fabricated link).

    LLM_API_KEY=sk-ant-... uv run pytest integrations/pydantic_ai_drafting/tests -q
"""

import os

import pytest

from brokerops_core.models.drafting import DraftContext
from brokerops_core.models.message import MessageChannel
from brokerops_pydantic_ai_drafting.adapter import DEFAULT_MODEL, PydanticAIDraftingAdapter

pytestmark = pytest.mark.skipif(
    not os.environ.get("LLM_API_KEY"),
    reason="PydanticAI drafting eval needs LLM_API_KEY (live model call)",
)


def _adapter() -> PydanticAIDraftingAdapter:
    return PydanticAIDraftingAdapter(
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
    )


async def test_drafts_a_grounded_sendable_email() -> None:
    context = DraftContext(
        channel=MessageChannel.EMAIL,
        recipient="sam.buyer@example.com",
        template_ref="showing_followup:v1",
        params={
            "recipient_name": "Sam",
            "listing_address": "123 Main Street",
            "sender_name": "The Brokerage Team",
        },
        contact_id="contact-1",
        listing_key="123 Main Street",
    )
    drafted = await _adapter().draft(context)

    # Sendable copy.
    assert drafted.subject.strip()
    assert drafted.body.strip()
    # Grounded in the details, not the internal wiring.
    assert "showing_followup:v1" not in drafted.body
    assert "http://" not in drafted.body and "https://" not in drafted.body
    # The trust-boundary overlay held against the live model.
    assert drafted.recipient == context.recipient
    assert drafted.channel is MessageChannel.EMAIL
    assert drafted.template_ref == context.template_ref
    assert drafted.contact_id == context.contact_id
