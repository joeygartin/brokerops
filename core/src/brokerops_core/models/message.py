"""The channel-agnostic outbound business communication (BOP-015, ADR-0015).

One model for every outbound comms channel: email today, SMS when BOP-018 lands —
the channel is a field, not a subclass, so the `outbound_messages` history stays one
table and one review surface. This is *domain data* (a comms history, the
`call_records` precedent), distinct from the action audit-ledger: the ledger records
the mutation crossing the boundary; this records the communication itself.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict

from brokerops_core.models.sensitivity import CONTACT_PII


class MessageChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class MessageStatus(StrEnum):
    """Lifecycle of an outbound message.

    DRAFTED → SENT | FAILED is the direct-send path (BOP-015). Workflow-drafted
    comms (BOP-019) insert a human gate between draft and send:
    PENDING_APPROVAL → SENT | FAILED | REJECTED — no drafted text leaves the
    boundary without a human decision. DELIVERED is set only by a provider
    delivery-status webhook (BOP-018): SENT means the provider accepted the
    message; DELIVERED means the provider confirmed the handset got it. A
    delivery callback can also move SENT → FAILED.
    """

    DRAFTED = "drafted"
    PENDING_APPROVAL = "pending_approval"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    REJECTED = "rejected"


# How far along the send lifecycle each status is. Delivery callbacks may arrive
# out of order (Twilio documents no ordering guarantee), so webhook transitions
# only ever move a message *forward* — a late "sent" callback never downgrades a
# DELIVERED row.
STATUS_RANK: dict[MessageStatus, int] = {
    MessageStatus.DRAFTED: 0,
    MessageStatus.PENDING_APPROVAL: 1,
    MessageStatus.SENT: 2,
    MessageStatus.DELIVERED: 3,
    MessageStatus.FAILED: 3,
    # Terminal like FAILED: a human said no (BOP-019). Ranked so a stray
    # delivery callback naming a rejected message's sid is a clean no-op
    # instead of a KeyError → 500.
    MessageStatus.REJECTED: 3,
}


class Message(BaseModel):
    """One outbound business communication to a person.

    `extra="forbid"`: this model is the single source of truth for the comms-history
    shape — a field that isn't declared here doesn't exist.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    # Stamped by the scoped data layer (BOP-006); never caller-supplied.
    tenant_id: str = ""
    channel: MessageChannel = MessageChannel.EMAIL
    # An email address or phone number — role-restricted PII at egress (BOP-012).
    recipient: Annotated[str, CONTACT_PII]
    # Empty for channels that have no subject line (SMS).
    subject: str = ""
    body: str = ""
    # The versioned template this message was rendered from, e.g. "showing_followup:v1".
    template_ref: str = ""
    # Related entities, so the comms history is queryable by who/what it was about.
    contact_id: str = ""
    listing_key: str = ""
    transaction_id: str = ""
    status: MessageStatus = MessageStatus.DRAFTED
    provider_message_id: str = ""
    created_at: datetime | None = None
    sent_at: datetime | None = None


# Fields that vary per attempt (or are stamped later). Everything else is the
# message's *semantic identity* — what makes two send attempts "the same send" for
# idempotency keying and for deriving a deterministic message id within a run.
VOLATILE_MESSAGE_FIELDS = frozenset(
    {"id", "tenant_id", "status", "provider_message_id", "created_at", "sent_at"}
)


def semantic_send_args(message: Message) -> dict[str, Any]:
    """The semantic args of a send: the message minus its per-attempt fields.

    Shared by the idempotency seam (the dedupe key) and `MessageSendService` (the
    deterministic message id), so both derive "the same send" identically.
    """
    return message.model_dump(mode="json", exclude=set(VOLATILE_MESSAGE_FIELDS))
