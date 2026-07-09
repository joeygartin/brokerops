"""MessageSendService: the drafted → sent/failed lifecycle over EmailPort +
MessageStore, and the deterministic replay identity within a run (ADR-0015)."""

import pytest

from brokerops_core.models.message import STATUS_RANK, Message, MessageChannel, MessageStatus
from brokerops_core.models.message_templates import TemplateParamError, UnknownTemplateError
from brokerops_core.services.audit import AuditContext, audit_scope
from brokerops_core.services.message_send import MessageSendService

PARAMS = {
    "recipient_name": "Sam",
    "listing_address": "412 Alder Court",
    "sender_name": "The Rivermouth Team",
}


class CountingEmail:
    def __init__(self, fail: bool = False) -> None:
        self.sends = 0
        self.fail = fail
        # The exact payload the provider was handed on the most recent send — lets a
        # test assert what actually crossed to the provider (e.g. after DLP scrub).
        self.last: Message | None = None

    async def send(self, message: Message) -> str:
        self.sends += 1
        self.last = message
        if self.fail:
            raise RuntimeError("provider unavailable")
        return f"provider-{self.sends}"


class DictMessageStore:
    def __init__(self) -> None:
        self.rows: dict[str, Message] = {}

    async def save_message(self, message: Message) -> None:
        self.rows[message.id] = message

    async def get_message(self, message_id: str) -> Message | None:
        return self.rows.get(message_id)

    async def get_message_by_provider_id(self, provider_message_id: str) -> Message | None:
        for message in self.rows.values():
            if provider_message_id and message.provider_message_id == provider_message_id:
                return message
        return None

    async def list_messages(
        self, contact_id: str | None = None, limit: int = 100, transaction_id: str | None = None
    ) -> list[Message]:
        return list(self.rows.values())[:limit]

    async def advance_message_status(
        self, message_id: str, status: MessageStatus
    ) -> Message | None:
        row = self.rows.get(message_id)
        if row is None:
            return None
        if STATUS_RANK[status] > STATUS_RANK[row.status]:
            row = row.model_copy(update={"status": status})
            self.rows[message_id] = row
        return row


def _service(
    email: CountingEmail | None = None,
) -> tuple[MessageSendService, CountingEmail, DictMessageStore]:
    email = email or CountingEmail()
    store = DictMessageStore()
    return MessageSendService(email=email, store=store), email, store


def _run(run_id: str = "run-1") -> AuditContext:
    return AuditContext(workflow_run_id=run_id, workflow="message_send")


async def test_send_persists_sent_message_with_provider_id() -> None:
    service, email, store = _service()
    message = await service.send_email(
        recipient="sam@example.com",
        template_ref="showing_followup:v1",
        params=PARAMS,
        contact_id="101",
        listing_key="RM1001",
    )
    assert message.status is MessageStatus.SENT
    assert message.provider_message_id == "provider-1"
    assert message.template_ref == "showing_followup:v1"
    assert message.subject == "Following up on your tour of 412 Alder Court"
    assert message.sent_at is not None and message.created_at is not None
    assert email.sends == 1
    assert store.rows[message.id].status is MessageStatus.SENT  # the persisted row


async def test_provider_failure_persists_failed_and_reraises() -> None:
    service, _, store = _service(CountingEmail(fail=True))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
    (row,) = store.rows.values()
    assert row.status is MessageStatus.FAILED
    assert row.provider_message_id == ""


async def test_template_errors_raise_before_anything_is_persisted() -> None:
    service, email, store = _service()
    with pytest.raises(UnknownTemplateError):
        await service.send_email(recipient="s@x.com", template_ref="nope:v1", params={})
    with pytest.raises(TemplateParamError):
        await service.send_email(recipient="s@x.com", template_ref="showing_followup:v1", params={})
    assert store.rows == {} and email.sends == 0


async def test_replay_within_a_run_returns_the_original_row_without_resending() -> None:
    service, email, store = _service()
    with audit_scope(_run()):
        first = await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
        again = await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
    assert email.sends == 1  # the provider saw exactly one send
    assert first.id == again.id
    assert again.provider_message_id == "provider-1"
    assert len(store.rows) == 1  # one history row, not two


async def test_message_ids_are_deterministic_per_run_and_semantic_args() -> None:
    service, _, store = _service()
    with audit_scope(_run("run-a")):
        a1 = await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
        other = await service.send_email(
            recipient="lee@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
    with audit_scope(_run("run-b")):
        b1 = await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
    assert a1.id != other.id  # different semantics → different identity
    assert a1.id != b1.id  # a new run is a genuinely new send
    assert len(store.rows) == 3


async def test_sends_outside_a_run_get_random_ids_and_are_not_deduped() -> None:
    service, email, store = _service()
    first = await service.send_email(
        recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
    )
    second = await service.send_email(
        recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
    )
    assert first.id != second.id
    assert email.sends == 2
    assert len(store.rows) == 2


# ── SMS through the same service (BOP-018) ──────────────────────────────────


async def test_send_sms_persists_sms_channel_with_empty_subject() -> None:
    sms = CountingEmail()  # structurally an SMSPort double too
    store = DictMessageStore()
    service = MessageSendService(email=CountingEmail(), store=store, sms=sms)
    message = await service.send_sms(
        recipient="+15551230101",
        template_ref="showing_followup_sms:v1",
        params=PARAMS,
        contact_id="101",
    )
    assert message.channel is MessageChannel.SMS
    assert message.subject == ""  # SMS has no subject line
    assert "412 Alder Court" in message.body
    assert message.status is MessageStatus.SENT
    assert sms.sends == 1


async def test_send_sms_without_a_wired_provider_fails_loud() -> None:
    service, email, store = _service()  # sms port not wired
    with pytest.raises(RuntimeError, match="no SMS provider is wired"):
        await service.send_sms(
            recipient="+15551230101", template_ref="showing_followup_sms:v1", params=PARAMS
        )
    assert store.rows == {} and email.sends == 0


async def test_email_and_sms_replays_are_distinct_rows_within_one_run() -> None:
    email, sms = CountingEmail(), CountingEmail()
    store = DictMessageStore()
    service = MessageSendService(email=email, store=store, sms=sms)
    with audit_scope(_run()):
        by_email = await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
        by_sms = await service.send_sms(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
    # Same template, same recipient, same run — but a different channel is a
    # different semantic send: two rows, two provider calls.
    assert by_email.id != by_sms.id
    assert email.sends == 1 and sms.sends == 1


async def test_outbound_secret_shape_is_redacted_before_the_provider_sees_it() -> None:
    # An LLM drafting backend (BOP-020) could put a leaked credential in generated
    # copy. It must be DLP-scrubbed before it reaches the provider — and the persisted
    # history must show exactly what shipped, not the pre-scrub text.
    service, email, store = _service()
    leaked = Message(
        id="m-secret",
        channel=MessageChannel.EMAIL,
        recipient="sam@example.com",
        subject="Your showing follow-up",
        body="Thanks for visiting! (internal note: key sk-ABCDEF0123456789XYZ)",
        template_ref="showing_followup:v1",
        status=MessageStatus.PENDING_APPROVAL,
    )
    await store.save_message(leaked)
    sent = await service.send_approved("m-secret")
    assert email.last is not None
    # The provider never saw the raw credential.
    assert "sk-ABCDEF0123456789XYZ" not in email.last.body
    assert "[redacted:secret]" in email.last.body
    # Recipient (CONTACT_PII at the OPERATOR seam tier) is preserved — the send needs it.
    assert email.last.recipient == "sam@example.com"
    # History reflects exactly what shipped (row and sent payload agree, both scrubbed).
    assert sent.body == email.last.body
    assert "sk-ABCDEF0123456789XYZ" not in store.rows["m-secret"].body


async def test_clean_outbound_message_ships_and_is_persisted_unchanged() -> None:
    # Copy-on-write: an ordinary (non-leaking) message is sent verbatim — the scrub
    # is a no-op for controlled copy, so the deterministic path is unaffected.
    service, email, store = _service()
    clean = Message(
        id="m-clean",
        channel=MessageChannel.EMAIL,
        recipient="sam@example.com",
        subject="Your showing follow-up",
        body="Thanks for visiting 412 Alder Court — let us know your thoughts!",
        template_ref="showing_followup:v1",
        status=MessageStatus.PENDING_APPROVAL,
    )
    await store.save_message(clean)
    sent = await service.send_approved("m-clean")
    assert email.last is not None
    assert email.last.body == clean.body
    assert sent.status is MessageStatus.SENT and sent.body == clean.body
