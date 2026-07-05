"""Sending templated business comms through the port seam (BOP-015/018, ADR-0015).

The service is the one place the send lifecycle lives: render the versioned
template → persist the message as DRAFTED → send through the channel port →
persist SENT (with the provider id) or FAILED. Wire it with the *decorated*
ports (Idempotent…(Recording…(adapter))) and every send is audited (ADR-0010)
and deduped (ADR-0011) exactly like the CRM and voice writes; the message store
is wired through the tenant-scoping seam (ADR-0012) like every other domain store.
Email and SMS share the lifecycle wholesale — the channel decides which port the
message leaves through and whether the rendered subject is kept (SMS has none).

Replay safety is two belts deep. Within a run (audit_scope bound), the message id
is *deterministic* — the same SHA-256 the idempotency seam uses over the send's
semantic args — so a replay targets the same `outbound_messages` row (the upsert
makes it one row, not two) and short-circuits here when that row is already SENT;
even if it doesn't, the Idempotent decorator refuses to re-send. Outside a run
the id is random and the send runs undeduped, mirroring `_Deduper`'s contract.
"""

from datetime import UTC, datetime
from uuid import uuid4

from brokerops_core.models.message import (
    Message,
    MessageChannel,
    MessageStatus,
    semantic_send_args,
)
from brokerops_core.models.message_templates import get_template
from brokerops_core.ports.messaging import EmailPort, MessageStore, SMSPort
from brokerops_core.services.audit import current_audit_context
from brokerops_core.services.idempotency import idempotency_key

# The channel port's seam tool name — must match what the Idempotent decorator
# claims, so the deterministic message id and the dedupe key agree per channel.
_SEND_TOOL = {MessageChannel.EMAIL: "send_email", MessageChannel.SMS: "send_sms"}


def _message_id(draft: Message) -> str:
    context = current_audit_context()
    if context is None or not context.workflow_run_id:
        return uuid4().hex
    return idempotency_key(
        context.workflow_run_id, _SEND_TOOL[draft.channel], semantic_send_args(draft)
    )


class MessageSendService:
    """Send a templated message (email or SMS) and record it in the comms history."""

    def __init__(self, email: EmailPort, store: MessageStore, sms: SMSPort | None = None) -> None:
        self._email = email
        self._sms = sms
        self._store = store

    async def send_email(
        self,
        *,
        recipient: str,
        template_ref: str,
        params: dict[str, str],
        contact_id: str = "",
        listing_key: str = "",
        transaction_id: str = "",
    ) -> Message:
        """Render, persist, and send one email; returns the persisted Message.

        Raises UnknownTemplateError / TemplateParamError before anything is
        persisted, and re-raises a provider failure after persisting the message
        as FAILED (the failure detail itself lives in the audit ledger).
        """
        return await self._send(
            port=self._email,
            channel=MessageChannel.EMAIL,
            recipient=recipient,
            template_ref=template_ref,
            params=params,
            contact_id=contact_id,
            listing_key=listing_key,
            transaction_id=transaction_id,
        )

    async def send_sms(
        self,
        *,
        recipient: str,
        template_ref: str,
        params: dict[str, str],
        contact_id: str = "",
        listing_key: str = "",
        transaction_id: str = "",
    ) -> Message:
        """Render, persist, and send one SMS; returns the persisted Message.

        Same lifecycle and error contract as `send_email`; the rendered subject is
        discarded (SMS has no subject line — the model persists it empty). Raises
        RuntimeError when no SMS provider is wired.
        """
        if self._sms is None:
            raise RuntimeError("no SMS provider is wired (SMS_PROVIDER)")
        return await self._send(
            port=self._sms,
            channel=MessageChannel.SMS,
            recipient=recipient,
            template_ref=template_ref,
            params=params,
            contact_id=contact_id,
            listing_key=listing_key,
            transaction_id=transaction_id,
        )

    async def _send(
        self,
        *,
        port: EmailPort | SMSPort,
        channel: MessageChannel,
        recipient: str,
        template_ref: str,
        params: dict[str, str],
        contact_id: str,
        listing_key: str,
        transaction_id: str,
    ) -> Message:
        template = get_template(template_ref)
        subject, body = template.render(params)
        draft = Message(
            id="",  # excluded from semantic identity; assigned below
            channel=channel,
            recipient=recipient,
            # Empty for channels that have no subject line (the Message contract).
            subject=subject if channel is MessageChannel.EMAIL else "",
            body=body,
            template_ref=template.ref,
            contact_id=contact_id,
            listing_key=listing_key,
            transaction_id=transaction_id,
            status=MessageStatus.DRAFTED,
            created_at=datetime.now(UTC),
        )
        message = draft.model_copy(update={"id": _message_id(draft)})
        existing = await self._store.get_message(message.id)
        if existing is not None and existing.status in (
            MessageStatus.SENT,
            MessageStatus.DELIVERED,
        ):
            # Replay of an already-sent message within the same run: return the
            # original row untouched — no second send, no second history row.
            # DELIVERED counts: a delivery callback may already have advanced it.
            return existing
        await self._store.save_message(message)
        try:
            provider_id = await port.send(message)
        except Exception:
            await self._store.save_message(
                message.model_copy(update={"status": MessageStatus.FAILED})
            )
            raise
        sent = message.model_copy(
            update={
                "status": MessageStatus.SENT,
                "provider_message_id": provider_id,
                "sent_at": datetime.now(UTC),
            }
        )
        await self._store.save_message(sent)
        # Return the row as stored, not the in-flight object: the scoped data layer
        # stamps tenant_id onto the persisted copy (ADR-0012), and callers should see
        # one representation whether they read the send response or the history.
        stored = await self._store.get_message(sent.id)
        return stored if stored is not None else sent
