"""Ports for outbound business communication (BOP-015, ADR-0015).

`EmailPort` is deliberately distinct from `EmailSender` (ADR-0008): EmailSender is
an *auth* concern — magic-link delivery, configured with the deployment's own SMTP —
while EmailPort is the *business comms* channel (client-facing email via a
transactional provider), with a different blast radius, provider, and config.
Conflating them would couple login delivery to the comms provider. EmailSender
stays untouched; providers implementing this port are selected by EMAIL_PROVIDER
(stub today; SES/SendGrid in BOP-016/017).
"""

from typing import Protocol

from brokerops_core.models.message import Message


class EmailPort(Protocol):
    """Boundary to the outbound business-email provider."""

    async def send(self, message: Message) -> str:
        """Send `message`; returns the provider's message id."""
        ...


class SMSPort(Protocol):
    """Boundary to the outbound SMS provider (BOP-018).

    Same contract as EmailPort over the same channel-agnostic `Message`
    (channel=sms, empty subject) — a separate port because the two channels are
    separately wired (SMS_PROVIDER vs EMAIL_PROVIDER), separately decorated
    (RecordingSMS/IdempotentSMS), and fail independently.
    """

    async def send(self, message: Message) -> str:
        """Send `message` (channel=sms); returns the provider's message id."""
        ...


class MessageStore(Protocol):
    """Persistence boundary for the outbound comms history (`outbound_messages`).

    Domain data, not audit-ledger data: the ledger records the mutation crossing the
    provider boundary (via RecordingEmail); this records the communication itself.
    `save_message` is an upsert keyed on `message.id` so the drafted → sent/failed
    lifecycle updates one row.
    """

    async def save_message(self, message: Message) -> None: ...

    async def get_message(self, message_id: str) -> Message | None: ...

    async def get_message_by_provider_id(self, provider_message_id: str) -> Message | None:
        """Look up a message by the provider's id — how a delivery-status webhook
        (which only knows the provider's MessageSid) finds its row (BOP-018)."""
        ...

    async def list_messages(
        self, contact_id: str | None = None, limit: int = 100
    ) -> list[Message]: ...
