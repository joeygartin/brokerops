"""The SMSPort write seam: RecordingSMS (ADR-0010) + IdempotentSMS (ADR-0011),
layered exactly like the email decorators (BOP-018) so SMS inherits identical
behavior — and stays a *distinct* write from the same words sent by email."""

from datetime import UTC, datetime

import pytest

from brokerops_core.models.idempotency import ClaimStatus, IdempotencyClaim
from brokerops_core.models.message import Message, MessageChannel, semantic_send_args
from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.services.audit import AuditContext, RecordingSMS, audit_scope
from brokerops_core.services.idempotency import (
    IdempotentEmail,
    IdempotentSMS,
    ReplayInProgressError,
)


class CountingSMS:
    def __init__(self, fail: bool = False) -> None:
        self.sends = 0
        self.fail = fail

    async def send(self, message: Message) -> str:
        self.sends += 1
        if self.fail:
            raise RuntimeError("twilio 30007")
        return f"SM{self.sends:030x}"


class InMemoryStore:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[ClaimStatus, str | None]] = {}

    async def begin(self, key: str, *, workflow_run_id: str, tool: str) -> IdempotencyClaim:
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = (ClaimStatus.PENDING, None)
            return IdempotencyClaim(status=ClaimStatus.NEW)
        status, result = existing
        return IdempotencyClaim(status=status, result=result)

    async def complete(self, key: str, result: str) -> None:
        self._rows[key] = (ClaimStatus.COMPLETED, result)


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


def _message(message_id: str = "m-1", channel: MessageChannel = MessageChannel.SMS) -> Message:
    return Message(
        id=message_id,
        channel=channel,
        recipient="+15551230101",
        body="Hi Sam, thanks for touring 412 Alder Court!",
        template_ref="showing_followup_sms:v1",
        contact_id="101",
        created_at=datetime(2026, 7, 4, tzinfo=UTC),
    )


def _run() -> AuditContext:
    return AuditContext(
        workflow_run_id="run-1", workflow="message_send", approval_id="ap-1", actor="op@x"
    )


async def test_recording_sms_records_success_with_provider_id() -> None:
    audit = CollectingAuditLog()
    recording = RecordingSMS(CountingSMS(), audit)
    with audit_scope(_run()):
        provider_id = await recording.send(_message())
    assert provider_id.startswith("SM")
    (record,) = audit.records
    assert record.integration == "twilio_sms"
    assert record.tool == "send_sms"
    assert record.outcome is MutationOutcome.SUCCESS
    assert record.external_id == provider_id
    assert record.args["recipient"] == "+15551230101"
    assert record.workflow_run_id == "run-1"
    assert record.approval_id == "ap-1"
    assert record.actor == "op@x"


async def test_recording_sms_records_failure_and_reraises() -> None:
    audit = CollectingAuditLog()
    recording = RecordingSMS(CountingSMS(fail=True), audit)
    with audit_scope(_run()):
        with pytest.raises(RuntimeError, match="twilio 30007"):
            await recording.send(_message())
    (record,) = audit.records
    assert record.outcome is MutationOutcome.FAILURE
    assert record.error == "twilio 30007"


async def test_replay_within_a_run_sends_once_and_returns_original_id() -> None:
    sms = CountingSMS()
    deduped = IdempotentSMS(sms, InMemoryStore())
    with audit_scope(_run()):
        first = await deduped.send(_message(message_id="m-1"))
        # A rebuilt Message (fresh id/timestamp) is still the same logical send.
        again = await deduped.send(
            _message(message_id="m-2").model_copy(
                update={"created_at": datetime(2026, 7, 5, tzinfo=UTC)}
            )
        )
    assert sms.sends == 1
    assert first == again


async def test_pending_replay_refuses_to_resend() -> None:
    sms = CountingSMS()
    store = InMemoryStore()
    deduped = IdempotentSMS(sms, store)
    with audit_scope(_run()):
        with pytest.raises(RuntimeError, match="twilio 30007"):
            await IdempotentSMS(CountingSMS(fail=True), store).send(_message())
        with pytest.raises(ReplayInProgressError, match="send_sms"):
            await deduped.send(_message())
    assert sms.sends == 0  # at-most-once: the retry did not double-text


async def test_deduped_replay_writes_no_second_mutation_record() -> None:
    sms = CountingSMS()
    audit = CollectingAuditLog()
    seam = IdempotentSMS(RecordingSMS(sms, audit), InMemoryStore())
    with audit_scope(_run()):
        await seam.send(_message())
        await seam.send(_message())
    assert sms.sends == 1
    assert len(audit.records) == 1


async def test_sms_and_email_sends_do_not_share_a_dedupe_key() -> None:
    # The channel is a semantic field AND the tool name differs, so the same
    # words leaving by SMS and by email within one run are two distinct writes.
    store = InMemoryStore()
    sms, email = CountingSMS(), CountingSMS()
    with audit_scope(_run()):
        await IdempotentSMS(sms, store).send(_message(channel=MessageChannel.SMS))
        await IdempotentEmail(email, store).send(_message(channel=MessageChannel.EMAIL))
    assert sms.sends == 1 and email.sends == 1
    assert semantic_send_args(_message(channel=MessageChannel.SMS)) != semantic_send_args(
        _message(channel=MessageChannel.EMAIL)
    )
