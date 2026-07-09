"""BOP-019: DeterministicDrafter (the zero-credential DraftingPort default),
the drafted-touchpoint rules, and the pending-approval → send/reject lifecycle
on MessageSendService."""

from datetime import date, timedelta

import pytest

from pydantic import ValidationError

from brokerops_core.models.contact import Contact
from brokerops_core.models.drafting import (
    EDITED_BODY_MAX_CHARS,
    DraftContext,
    DraftedMessage,
    EditedMessagePayload,
)
from brokerops_core.models.message import STATUS_RANK, Message, MessageChannel, MessageStatus
from brokerops_core.models.message_templates import TemplateParamError, UnknownTemplateError
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.services.audit import AuditContext, audit_scope
from brokerops_core.services.drafting import (
    DeterministicDrafter,
    edited_draft_fields,
    plan_showing_followup_email,
)
from brokerops_core.services.message_send import MessageSendService, UnknownOutboundMessageError
from brokerops_core.services.milestone_engine import plan_reminder_email

TODAY = date.today()


class CountingEmail:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Message] = []
        self.fail = fail

    async def send(self, message: Message) -> str:
        if self.fail:
            raise RuntimeError("provider unavailable")
        self.sent.append(message)
        return f"provider-{len(self.sent)}"


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
    service = MessageSendService(email=email, store=store, drafting=DeterministicDrafter())
    return service, email, store


def _context(body_params: dict[str, str] | None = None) -> DraftContext:
    return DraftContext(
        recipient="sam@example.com",
        template_ref="showing_followup:v1",
        params=body_params
        or {
            "recipient_name": "Sam",
            "listing_address": "412 Alder Court",
            "sender_name": "The Team",
        },
        contact_id="101",
        listing_key="RM1001",
    )


def _milestone(days_out: int, owner: str = "Dana Whitfield") -> Milestone:
    return Milestone(
        id=f"MS-{days_out}",
        transaction_id="TXN-1",
        type=MilestoneType.INSPECTION,
        title="Home inspection",
        due_date=TODAY + timedelta(days=days_out),
        owner=owner,
    )


def _txn(parties: list[TransactionParty]) -> Transaction:
    return Transaction(
        id="TXN-1",
        listing_key="RM1001",
        stage=TransactionStage.UNDER_CONTRACT,
        parties=parties,
        contract_date=TODAY - timedelta(days=5),
    )


# ── DeterministicDrafter ──────────────────────────────────────────────


async def test_deterministic_drafter_renders_the_versioned_template() -> None:
    drafted = await DeterministicDrafter().draft(_context())
    assert drafted.channel is MessageChannel.EMAIL
    assert drafted.recipient == "sam@example.com"
    assert drafted.subject == "Following up on your tour of 412 Alder Court"
    assert "Thank you for touring 412 Alder Court" in drafted.body
    assert drafted.template_ref == "showing_followup:v1"
    assert drafted.contact_id == "101" and drafted.listing_key == "RM1001"


async def test_deterministic_drafter_fails_loud_on_bad_template_or_params() -> None:
    with pytest.raises(UnknownTemplateError):
        await DeterministicDrafter().draft(
            DraftContext(recipient="s@x.com", template_ref="nope:v1")
        )
    with pytest.raises(TemplateParamError):
        await DeterministicDrafter().draft(
            DraftContext(recipient="s@x.com", template_ref="showing_followup:v1", params={})
        )


# ── touchpoint rules ─────────────────────────────────────────────────


def test_reminder_email_targets_the_owning_party_with_an_email() -> None:
    txn = _txn(
        [
            TransactionParty(role="buyer", name="Jordan Pike", contact_id="101"),
            TransactionParty(role="escrow", name="Dana Whitfield", email="dw@example.test"),
        ]
    )
    context = plan_reminder_email(txn, [(_milestone(2), 2)])
    assert context is not None
    assert context.recipient == "dw@example.test"
    assert context.template_ref == "milestone_reminder:v1"
    assert context.params["milestone_title"] == "Home inspection"
    assert context.params["due_date"] == (TODAY + timedelta(days=2)).isoformat()
    assert context.transaction_id == "TXN-1" and context.listing_key == "RM1001"


def test_reminder_email_picks_the_most_urgent_reachable_milestone() -> None:
    txn = _txn([TransactionParty(role="escrow", name="Dana Whitfield", email="dw@example.test")])
    unreachable_sooner = _milestone(1, owner="Nobody Known")
    reachable_later = _milestone(3)
    context = plan_reminder_email(txn, [(reachable_later, 3), (unreachable_sooner, 1)])
    assert context is not None
    assert context.params["due_date"] == (TODAY + timedelta(days=3)).isoformat()


def test_reminder_email_returns_none_when_no_party_is_reachable() -> None:
    # Owner matches no party / party has no email → no draft, tail skipped.
    assert plan_reminder_email(_txn([]), [(_milestone(2), 2)]) is None
    txn = _txn([TransactionParty(role="escrow", name="Dana Whitfield")])
    assert plan_reminder_email(txn, [(_milestone(2), 2)]) is None


def test_showing_followup_targets_the_contact_email() -> None:
    contact = Contact(crm_id="101", name="Jordan Pike", email="jordan@example.test")
    context = plan_showing_followup_email(contact, "RM1001")
    assert context is not None
    assert context.recipient == "jordan@example.test"
    assert context.template_ref == "showing_followup:v1"
    assert context.contact_id == "101" and context.listing_key == "RM1001"


def test_showing_followup_returns_none_without_contact_or_email() -> None:
    assert plan_showing_followup_email(None, "RM1001") is None
    assert plan_showing_followup_email(Contact(crm_id="1", name="No Email"), "RM1001") is None


# ── draft → approve/reject lifecycle ─────────────────────────────────


async def test_draft_for_approval_persists_pending_row_without_sending() -> None:
    service, email, store = _service()
    message = await service.draft_for_approval(_context())
    assert message.status is MessageStatus.PENDING_APPROVAL
    assert store.rows[message.id].status is MessageStatus.PENDING_APPROVAL
    assert email.sent == []


async def test_draft_replay_within_a_run_returns_the_original_row() -> None:
    service, _, store = _service()
    run = AuditContext(workflow_run_id="run-1", workflow="vapi_followup")
    with audit_scope(run):
        first = await service.draft_for_approval(_context())
        again = await service.draft_for_approval(_context())
    assert first.id == again.id
    assert len(store.rows) == 1


async def test_nondeterministic_backend_replay_reuses_the_row_without_redrafting() -> None:
    # BOP-020: an LLM backend returns different copy each call. The draft row's id
    # is keyed off the DraftContext (not the generated text) and the existing-row
    # check runs before the backend, so a replay must reuse the first draft AND not
    # invoke the backend again — otherwise fresh copy would mint a second approval
    # card with a new id.
    class DriftingDrafter:
        def __init__(self) -> None:
            self.calls = 0

        async def draft(self, context: DraftContext) -> "DraftedMessage":
            self.calls += 1
            return DraftedMessage(
                channel=context.channel,
                recipient=context.recipient,
                subject=f"Draft #{self.calls}",
                body=f"Body version {self.calls}",
                template_ref=context.template_ref,
                contact_id=context.contact_id,
                listing_key=context.listing_key,
            )

    drafter = DriftingDrafter()
    store = DictMessageStore()
    service = MessageSendService(email=CountingEmail(), store=store, drafting=drafter)
    run = AuditContext(workflow_run_id="run-drift", workflow="vapi_followup")
    with audit_scope(run):
        first = await service.draft_for_approval(_context())
        again = await service.draft_for_approval(_context())
    assert first.id == again.id
    assert again.body == first.body  # the reused row, not regenerated copy
    assert drafter.calls == 1  # the backend ran once; replay short-circuited before it
    assert len(store.rows) == 1


async def test_drafted_pending_row_is_dlp_scrubbed_before_the_card_reads_it() -> None:
    # BOP-020: a credential an LLM backend leaks into generated copy must not survive
    # into the persisted PENDING_APPROVAL row — the approval card reads that row, and
    # BOP-012 redacts secret shapes on any egress (role-independent). Scrub is at
    # draft time, not only at send.
    class LeakyDrafter:
        async def draft(self, context: DraftContext) -> DraftedMessage:
            return DraftedMessage(
                channel=context.channel,
                recipient=context.recipient,
                subject="Follow-up",
                body="Thanks for visiting! (debug token sk-ABCDEF0123456789XYZ)",
                template_ref=context.template_ref,
                contact_id=context.contact_id,
                listing_key=context.listing_key,
            )

    store = DictMessageStore()
    service = MessageSendService(email=CountingEmail(), store=store, drafting=LeakyDrafter())
    message = await service.draft_for_approval(_context())
    assert message.status is MessageStatus.PENDING_APPROVAL
    # The persisted row the approval card reads is already redacted — nothing sent yet.
    assert "sk-ABCDEF0123456789XYZ" not in store.rows[message.id].body
    assert "[redacted:secret]" in store.rows[message.id].body
    assert message.recipient == "sam@example.com"  # routing PII kept at the OPERATOR tier


async def test_draft_requires_a_drafting_backend() -> None:
    service = MessageSendService(email=CountingEmail(), store=DictMessageStore())
    with pytest.raises(RuntimeError, match="drafting backend"):
        await service.draft_for_approval(_context())


async def test_send_approved_ships_the_edited_text_and_marks_sent() -> None:
    service, email, store = _service()
    message = await service.draft_for_approval(_context())
    sent = await service.send_approved(message.id, body="Edited body.")
    assert sent.status is MessageStatus.SENT
    assert sent.body == "Edited body."
    assert sent.subject == message.subject  # unedited fields untouched
    assert [m.body for m in email.sent] == ["Edited body."]
    assert store.rows[message.id].status is MessageStatus.SENT


async def test_send_approved_without_edits_ships_the_draft_verbatim() -> None:
    service, email, _ = _service()
    message = await service.draft_for_approval(_context())
    sent = await service.send_approved(message.id)
    assert sent.body == message.body and sent.subject == message.subject
    assert email.sent[0].body == message.body


async def test_send_approved_replay_returns_sent_row_without_resending() -> None:
    service, email, _ = _service()
    message = await service.draft_for_approval(_context())
    first = await service.send_approved(message.id, body="Edited body.")
    again = await service.send_approved(message.id, body="Edited body.")
    assert first.id == again.id and again.status is MessageStatus.SENT
    assert len(email.sent) == 1


async def test_send_approved_provider_failure_persists_failed_with_final_text() -> None:
    service, _, store = _service(CountingEmail(fail=True))
    message = await service.draft_for_approval(_context())
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await service.send_approved(message.id, body="Edited body.")
    row = store.rows[message.id]
    assert row.status is MessageStatus.FAILED
    assert row.body == "Edited body."  # the row shows exactly what was attempted


async def test_send_approved_refuses_unwired_channels_and_unknown_ids() -> None:
    service, email, store = _service()
    sms = Message(
        id="sms-1",
        channel=MessageChannel.SMS,
        recipient="+15550100",
        body="hi",
        status=MessageStatus.PENDING_APPROVAL,
    )
    await store.save_message(sms)
    with pytest.raises(RuntimeError, match="no SMS provider is wired"):
        await service.send_approved("sms-1")
    with pytest.raises(LookupError):
        await service.send_approved("missing-id")
    assert email.sent == []


async def test_send_approved_dispatches_sms_through_the_port_map_when_wired() -> None:
    email = CountingEmail()
    sms_port = CountingEmail()  # same shape: async send(Message) -> str
    store = DictMessageStore()
    service = MessageSendService(
        email=email, store=store, sms=sms_port, drafting=DeterministicDrafter()
    )
    row = Message(
        id="sms-2",
        channel=MessageChannel.SMS,
        recipient="+15550100",
        body="hi",
        status=MessageStatus.PENDING_APPROVAL,
    )
    await store.save_message(row)
    sent = await service.send_approved("sms-2")
    assert sent.status is MessageStatus.SENT
    assert [m.id for m in sms_port.sent] == ["sms-2"]
    assert email.sent == []  # channel dispatch picked the SMS port, not email


async def test_send_approved_only_sends_from_pending_or_failed() -> None:
    # F2: a terminal row is returned untouched — a stray approve must never
    # re-send a SENT/DELIVERED message or overturn a human REJECTED.
    service, email, store = _service()
    for terminal in (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.REJECTED):
        row = Message(
            id=f"m-{terminal.value}",
            recipient="sam@example.com",
            body="original",
            status=terminal,
        )
        await store.save_message(row)
        result = await service.send_approved(row.id, body="Should never ship.")
        assert result.status is terminal  # untouched — DELIVERED never downgrades
        assert result.body == "original"
    assert email.sent == []


async def test_rejected_row_is_not_resendable_via_approve() -> None:
    # F2 probe scenario: mark_rejected then send_approved must NOT send and
    # must NOT flip REJECTED → SENT.
    service, email, store = _service()
    message = await service.draft_for_approval(_context())
    await service.mark_rejected(message.id)
    result = await service.send_approved(message.id)
    assert result.status is MessageStatus.REJECTED
    assert store.rows[message.id].status is MessageStatus.REJECTED
    assert email.sent == []


async def test_send_approved_refuses_a_blank_edited_body() -> None:
    # F4: a present-but-blank edit must fail loudly, never silently revert to
    # the original draft text.
    service, email, store = _service()
    message = await service.draft_for_approval(_context())
    for blank in ("", "   \n\t"):
        with pytest.raises(ValueError, match="blank"):
            await service.send_approved(message.id, body=blank)
    assert email.sent == []
    assert store.rows[message.id].status is MessageStatus.PENDING_APPROVAL


async def test_dangling_ids_raise_the_named_domain_error() -> None:
    # BOP-037: send_approved/mark_rejected raise UnknownOutboundMessageError —
    # a LookupError subclass (old catch sites keep working) that routes can map
    # to a clean 409 instead of a 500.
    service, _, _ = _service()
    with pytest.raises(UnknownOutboundMessageError) as sent_exc:
        await service.send_approved("missing-id")
    assert sent_exc.value.message_id == "missing-id"
    assert isinstance(sent_exc.value, LookupError)
    with pytest.raises(UnknownOutboundMessageError):
        await service.mark_rejected("missing-id")


def test_edited_message_payload_accepts_a_plain_body_edit() -> None:
    # The boundary shape of an approve-outbound-message decision's edits (BOP-037).
    payload = EditedMessagePayload.model_validate({"body": "Edited before send.\nThanks!"})
    assert payload.body == "Edited before send.\nThanks!"
    assert EditedMessagePayload.model_validate({}).body is None  # "{}" stays a no-op


def test_edited_message_payload_rejects_subject_entirely() -> None:
    # Decided policy: the UI never offers a subject edit, so the API admits no
    # subject field at all — the CRLF header-injection surface does not exist.
    with pytest.raises(ValidationError):
        EditedMessagePayload.model_validate({"subject": "Re: tour\r\nBcc: x@evil.test"})
    with pytest.raises(ValidationError):
        EditedMessagePayload.model_validate({"subject": "harmless", "body": "hi"})


def test_edited_message_payload_rejects_hostile_bodies() -> None:
    for bad in ("with a \x00 null", "esc\x1b[31m", "bell\x07", "del\x7f"):
        with pytest.raises(ValidationError, match="control characters"):
            EditedMessagePayload.model_validate({"body": bad})
    with pytest.raises(ValidationError, match="blank"):
        EditedMessagePayload.model_validate({"body": "   \n\t"})
    with pytest.raises(ValidationError):
        EditedMessagePayload.model_validate({"body": "x" * (EDITED_BODY_MAX_CHARS + 1)})
    # \n, \r, \t are legitimate in multiline text and stay allowed.
    assert EditedMessagePayload.model_validate({"body": "a\r\nb\tc"}).body == "a\r\nb\tc"


def test_edited_draft_fields_extracts_edits_and_refuses_blank_bodies() -> None:
    assert edited_draft_fields(None) == ("", "")
    assert edited_draft_fields({}) == ("", "")
    assert edited_draft_fields({"body": "New text."}) == ("", "New text.")
    assert edited_draft_fields({"subject": "New subject", "body": "New text."}) == (
        "New subject",
        "New text.",
    )
    for blank in ("", "   ", "\n\t"):
        with pytest.raises(ValueError, match="blank"):
            edited_draft_fields({"body": blank})


def test_status_rank_is_total_over_the_lifecycle() -> None:
    # Item 5: a delivery callback ranks the row's CURRENT status — every enum
    # member must have a rank (REJECTED included) or a stray callback naming a
    # rejected message's sid KeyErrors into a 500.
    assert set(STATUS_RANK) == set(MessageStatus)
    # REJECTED is terminal: no callback status outranks it.
    assert all(STATUS_RANK[s] <= STATUS_RANK[MessageStatus.REJECTED] for s in MessageStatus)


async def test_mark_rejected_records_the_decision_and_sends_nothing() -> None:
    service, email, store = _service()
    message = await service.draft_for_approval(_context())
    rejected = await service.mark_rejected(message.id)
    assert rejected.status is MessageStatus.REJECTED
    assert store.rows[message.id].status is MessageStatus.REJECTED
    assert email.sent == []
    # replayed rejection (or any terminal row) is a no-op
    assert (await service.mark_rejected(message.id)).status is MessageStatus.REJECTED
    with pytest.raises(LookupError):
        await service.mark_rejected("missing-id")
