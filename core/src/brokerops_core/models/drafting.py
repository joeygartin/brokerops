"""Drafting shapes (BOP-019): what a drafting backend consumes and produces.

`DraftContext` is everything a drafter needs to compose one outbound message —
who it goes to, which versioned template anchors it, and the entity refs that
make the resulting comms row queryable. `DraftedMessage` is the produced text,
still unpersisted and unsent: `MessageSendService.draft_for_approval` turns it
into a PENDING_APPROVAL `Message` row, and nothing ships without a human
decision. Both are the contract of `DraftingPort` — the deterministic default
renders templates verbatim; an LLM backend (BOP-020) fills the same shapes.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# Same abuse ceiling as the documents text-upload bound (UPLOAD_MAX_CHARS,
# BOP-021): a generous cap whose job is bounding hostile input, not styling copy.
EDITED_BODY_MAX_CHARS = 1_000_000

# C0 control characters (minus \t \n \r, legitimate in multiline text) + DEL.
_FORBIDDEN_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EditedMessagePayload(BaseModel):
    """The wire shape of an approve-outbound-message decision's edits (BOP-037).

    Pydantic-at-the-boundary for `edited_payload` on that gate kind: previously an
    open dict, where a hostile value died mid-send as a FAILED row + 500 instead
    of a 422 at the boundary.

    Subject policy (decided here): the API accepts **no subject edit at all** —
    the inbox card only ever offers the body, so no legitimate caller produces
    one, and rejecting the field outright (`extra="forbid"`) removes the
    CRLF-header-injection surface entirely rather than sanitizing it. The core
    `edited_draft_fields` helper still understands a subject for engine-level
    callers; the HTTP boundary simply never admits one.
    """

    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(default=None, max_length=EDITED_BODY_MAX_CHARS)

    @field_validator("body")
    @classmethod
    def _body_is_sendable_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.strip():
            # Same rule the core enforces later, surfaced as a 422: a blank edit
            # must never silently fall back to the draft — reject the gate instead.
            raise ValueError("edited body is blank — reject the draft instead")
        if _FORBIDDEN_CONTROL_CHARS.search(value):
            raise ValueError("edited body contains control characters")
        return value
