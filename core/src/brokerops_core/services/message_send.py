"""Sending templated business email through the port seam (BOP-015, ADR-0015).

The service is the one place the send lifecycle lives: render the versioned
template → persist the message as DRAFTED → send through `EmailPort` → persist
SENT (with the provider id) or FAILED. Wire it with the *decorated* port
(IdempotentEmail(RecordingEmail(adapter))) and every send is audited (ADR-0010)
and deduped (ADR-0011) exactly like the CRM and voice writes; the message store
is wired through the tenant-scoping seam (ADR-0012) like every other domain store.

Replay safety is two belts deep. Within a run (audit_scope bound), the message id
is *deterministic* — the same SHA-256 the idempotency seam uses over the send's
semantic args — so a replay targets the same `outbound_messages` row (the upsert
makes it one row, not two) and short-circuits here when that row is already SENT;
even if it doesn't, the IdempotentEmail decorator refuses to re-send. Outside a run
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
from brokerops_core.ports.messaging import EmailPort, MessageStore
from brokerops_core.services.audit import current_audit_context
from brokerops_core.services.idempotency import idempotency_key


def _message_id(draft: Message) -> str:
    context = current_audit_context()
    if context is None or not context.workflow_run_id:
        return uuid4().hex
    return idempotency_key(context.workflow_run_id, "send_email", semantic_send_args(draft))


class MessageSendService:
    """Send a templated email and record it in the outbound comms history."""

    def __init__(self, email: EmailPort, store: MessageStore) -> None:
        self._email = email
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
        template = get_template(template_ref)
        subject, body = template.render(params)
        draft = Message(
            id="",  # excluded from semantic identity; assigned below
            channel=MessageChannel.EMAIL,
            recipient=recipient,
            subject=subject,
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
        if existing is not None and existing.status is MessageStatus.SENT:
            # Replay of an already-sent message within the same run: return the
            # original row untouched — no second send, no second history row.
            return existing
        await self._store.save_message(message)
        try:
            provider_id = await self._email.send(message)
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
