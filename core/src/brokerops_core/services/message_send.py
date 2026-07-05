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
from typing import Any
from uuid import uuid4

from brokerops_core.models.drafting import DraftContext
from brokerops_core.models.message import (
    Message,
    MessageChannel,
    MessageStatus,
    semantic_send_args,
)
from brokerops_core.models.message_templates import get_template
from brokerops_core.ports.drafting import DraftingPort
from brokerops_core.ports.messaging import EmailPort, MessageStore, SMSPort
from brokerops_core.services.audit import current_audit_context
from brokerops_core.services.idempotency import idempotency_key

# The channel port's seam tool name — must match what the Idempotent decorator
# claims, so the deterministic message id and the dedupe key agree per channel.
_SEND_TOOL = {MessageChannel.EMAIL: "send_email", MessageChannel.SMS: "send_sms"}


class UnknownOutboundMessageError(LookupError):
    """A decision named an `outbound_messages` row that does not exist (BOP-037).

    A *named* domain error (not a bare assert/LookupError) so the gate/send
    nodes and the API routes can map a dangling message_id to a
    clean client error instead of a 500 — and so the check survives `python -O`,
    which strips asserts. Subclasses LookupError, so existing catch sites keep
    working.
    """

    def __init__(self, message_id: str) -> None:
        super().__init__(f"no outbound message {message_id!r}")
        self.message_id = message_id


def _message_id(draft: Message) -> str:
    context = current_audit_context()
    if context is None or not context.workflow_run_id:
        return uuid4().hex
    return idempotency_key(
        context.workflow_run_id, _SEND_TOOL[draft.channel], semantic_send_args(draft)
    )


class MessageSendService:
    """Send a templated message (email or SMS) and record it in the comms history.

    Two entry points share one lifecycle: `send_email`/`send_sms` are the direct
    path (render → DRAFTED → send → SENT/FAILED, BOP-015/018); the
    workflow-drafted path (BOP-019) is `draft_for_approval` → human gate →
    `send_approved` (possibly with the approver's edited text) or
    `mark_rejected`. The drafting backend is a `DraftingPort` — deterministic
    template rendering by default; nothing it produces is sent without the gate.
    """

    def __init__(
        self,
        email: EmailPort,
        store: MessageStore,
        sms: SMSPort | None = None,
        drafting: DraftingPort | None = None,
    ) -> None:
        self._email = email
        self._sms = sms
        self._store = store
        self._drafting = drafting

    def _port_for(self, channel: MessageChannel) -> EmailPort | SMSPort:
        """The wired seam port for `channel`; fails loud when none is wired."""
        if channel is MessageChannel.EMAIL:
            return self._email
        if self._sms is None:
            raise RuntimeError("no SMS provider is wired (SMS_PROVIDER)")
        return self._sms

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

    async def get_message(self, message_id: str) -> Message | None:
        """Read-through to the comms history (gate nodes re-fetch the draft row)."""
        return await self._store.get_message(message_id)

    async def draft_for_approval(self, context: DraftContext) -> Message:
        """Draft via the DraftingPort and persist the row as PENDING_APPROVAL.

        Nothing is sent here — the row waits for the approve-outbound-message
        gate. The id is the same deterministic within-a-run identity as sends,
        so a replayed draft node lands on the original row (even one already
        decided) instead of minting a duplicate.
        """
        if self._drafting is None:
            raise RuntimeError("no drafting backend wired (DraftingPort is required to draft)")
        drafted = await self._drafting.draft(context)
        draft = Message(
            id="",  # excluded from semantic identity; assigned below
            channel=drafted.channel,
            recipient=drafted.recipient,
            # Empty for channels that have no subject line (the Message contract).
            subject=drafted.subject if drafted.channel is MessageChannel.EMAIL else "",
            body=drafted.body,
            template_ref=drafted.template_ref,
            contact_id=drafted.contact_id,
            listing_key=drafted.listing_key,
            transaction_id=drafted.transaction_id,
            status=MessageStatus.PENDING_APPROVAL,
            created_at=datetime.now(UTC),
        )
        message = draft.model_copy(update={"id": _message_id(draft)})
        existing = await self._store.get_message(message.id)
        if existing is not None:
            return existing
        await self._store.save_message(message)
        stored = await self._store.get_message(message.id)
        return stored if stored is not None else message

    async def send_approved(
        self, message_id: str, *, subject: str | None = None, body: str | None = None
    ) -> Message:
        """Send a pending-approval message — the human decision just happened.

        `subject`/`body` carry the approver's edits (the edited-payload
        convention): the row is updated *before* the send so the history — and
        a FAILED row — always shows the exact text that shipped or was
        attempted. A blank edited body is refused loudly: the card promises the
        visible text is exactly what sends, so it must never silently fall back
        to the original draft.

        Sends only from PENDING_APPROVAL (or FAILED, the same decision being
        driven to completion after a provider error). Every other status
        returns the row untouched: SENT/DELIVERED is the replay short-circuit —
        DELIVERED counts as sent and is never downgraded (BOP-018's
        forward-only rule) — and REJECTED is a terminal human "no" that a
        stray approve must not overturn.
        """
        if body is not None and not body.strip():
            raise ValueError(
                "edited draft body is blank — reject the draft instead of sending an empty message"
            )
        row = await self._store.get_message(message_id)
        if row is None:
            raise UnknownOutboundMessageError(message_id)
        if row.status not in (MessageStatus.PENDING_APPROVAL, MessageStatus.FAILED):
            return row
        port = self._port_for(row.channel)
        updates: dict[str, Any] = {}
        if subject is not None and subject != row.subject:
            updates["subject"] = subject
        if body is not None and body != row.body:
            updates["body"] = body
        message = row.model_copy(update=updates)
        if updates:
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
        stored = await self._store.get_message(sent.id)
        return stored if stored is not None else sent

    async def mark_rejected(self, message_id: str) -> Message:
        """Record the human's 'no': the row reflects the decision; nothing sends.

        Only a PENDING_APPROVAL row transitions — a replayed rejection (or a row
        already terminal) returns as-is.
        """
        row = await self._store.get_message(message_id)
        if row is None:
            raise UnknownOutboundMessageError(message_id)
        if row.status is not MessageStatus.PENDING_APPROVAL:
            return row
        rejected = row.model_copy(update={"status": MessageStatus.REJECTED})
        await self._store.save_message(rejected)
        stored = await self._store.get_message(rejected.id)
        return stored if stored is not None else rejected
