"""Drafting shapes (BOP-019): what a drafting backend consumes and produces.

`DraftContext` is everything a drafter needs to compose one outbound message —
who it goes to, which versioned template anchors it, and the entity refs that
make the resulting comms row queryable. `DraftedMessage` is the produced text,
still unpersisted and unsent: `MessageSendService.draft_for_approval` turns it
into a PENDING_APPROVAL `Message` row, and nothing ships without a human
decision. Both are the contract of `DraftingPort` — the deterministic default
renders templates verbatim; an LLM backend (BOP-020) fills the same shapes.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from brokerops_core.models.message import MessageChannel
from brokerops_core.models.sensitivity import CONTACT_PII


class DraftContext(BaseModel):
    """Inputs for drafting one outbound message.

    `extra="forbid"`: this is a boundary shape — the drafting backend sees
    exactly these fields and nothing else.
    """

    model_config = ConfigDict(extra="forbid")

    channel: MessageChannel = MessageChannel.EMAIL
    recipient: str
    # The versioned template that anchors the draft, e.g. "milestone_reminder:v1".
    # The deterministic backend renders it verbatim; an LLM backend (BOP-020)
    # treats it as the grounding for generated text.
    template_ref: str
    params: dict[str, str] = Field(default_factory=dict)
    # Related entities, carried through to the persisted Message row.
    contact_id: str = ""
    listing_key: str = ""
    transaction_id: str = ""


class DraftedMessage(BaseModel):
    """One drafted (not yet persisted, not yet sent) outbound message."""

    model_config = ConfigDict(extra="forbid")

    channel: MessageChannel = MessageChannel.EMAIL
    # An email address or phone number — role-restricted PII at egress (BOP-012).
    recipient: Annotated[str, CONTACT_PII]
    subject: str = ""
    body: str
    template_ref: str = ""
    contact_id: str = ""
    listing_key: str = ""
    transaction_id: str = ""
