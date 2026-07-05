"""BOP-019: DeterministicDrafter (the zero-credential DraftingPort default),
the drafted-touchpoint rules, and the pending-approval → send/reject lifecycle
on MessageSendService."""

from datetime import date, timedelta

import pytest

from brokerops_core.models.contact import Contact
from brokerops_core.models.drafting import DraftContext
from brokerops_core.models.message import Message, MessageChannel, MessageStatus
from brokerops_core.models.message_templates import TemplateParamError, UnknownTemplateError
from brokerops_core.models.milestone import Milestone, MilestoneType
from brokerops_core.models.transaction import Transaction, TransactionParty, TransactionStage
from brokerops_core.services.audit import AuditContext, audit_scope
from brokerops_core.services.drafting import (
    DeterministicDrafter,
    plan_showing_followup_email,
)
from brokerops_core.services.message_send import MessageSendService
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

    async def list_messages(self, contact_id: str | None = None, limit: int = 100) -> list[Message]:
        return list(self.rows.values())[:limit]


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
    sms = Message(id="sms-1", channel=MessageChannel.SMS, recipient="+15550100")
    await store.save_message(sms)
    with pytest.raises(RuntimeError, match="no send port"):
        await service.send_approved("sms-1")
    with pytest.raises(LookupError):
        await service.send_approved("missing-id")
    assert email.sent == []


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
