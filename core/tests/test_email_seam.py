"""The EmailPort write seam: RecordingEmail (ADR-0010) + IdempotentEmail (ADR-0011),
layered exactly like the CRM/voice decorators so email inherits identical behavior."""

from datetime import UTC, datetime

import pytest

from brokerops_core.models.idempotency import ClaimStatus, IdempotencyClaim
from brokerops_core.models.message import Message, MessageStatus, semantic_send_args
from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.services.audit import AuditContext, RecordingEmail, audit_scope
from brokerops_core.services.idempotency import IdempotentEmail, ReplayInProgressError


class CountingEmail:
    def __init__(self, fail: bool = False) -> None:
        self.sends = 0
        self.fail = fail

    async def send(self, message: Message) -> str:
        self.sends += 1
        if self.fail:
            raise RuntimeError("smtp 550")
        return f"provider-{self.sends}"


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


def _message(recipient: str = "sam@example.com", message_id: str = "m-1") -> Message:
    return Message(
        id=message_id,
        recipient=recipient,
        subject="Following up",
        body="Hi Sam",
        template_ref="showing_followup:v1",
        contact_id="101",
        created_at=datetime(2026, 7, 4, tzinfo=UTC),
    )


def _run() -> AuditContext:
    return AuditContext(
        workflow_run_id="run-1", workflow="message_send", approval_id="ap-1", actor="op@x"
    )


def test_semantic_args_exclude_per_attempt_fields() -> None:
    a = _message(message_id="m-1")
    b = a.model_copy(
        update={
            "id": "m-2",
            "status": MessageStatus.SENT,
            "provider_message_id": "p-9",
            "created_at": datetime(2026, 7, 5, tzinfo=UTC),
            "tenant_id": "acme",
        }
    )
    assert semantic_send_args(a) == semantic_send_args(b)
    assert semantic_send_args(a) != semantic_send_args(_message(recipient="lee@example.com"))


async def test_recording_email_records_success_with_provider_id() -> None:
    audit = CollectingAuditLog()
    recording = RecordingEmail(CountingEmail(), audit)
    with audit_scope(_run()):
        provider_id = await recording.send(_message())
    assert provider_id == "provider-1"
    (record,) = audit.records
    assert record.integration == "email"
    assert record.tool == "send_email"
    assert record.outcome is MutationOutcome.SUCCESS
    assert record.external_id == "provider-1"
    assert record.args["recipient"] == "sam@example.com"
    assert record.workflow_run_id == "run-1"
    assert record.approval_id == "ap-1"
    assert record.actor == "op@x"


async def test_recording_email_records_failure_and_reraises() -> None:
    audit = CollectingAuditLog()
    recording = RecordingEmail(CountingEmail(fail=True), audit)
    with audit_scope(_run()):
        with pytest.raises(RuntimeError, match="smtp 550"):
            await recording.send(_message())
    (record,) = audit.records
    assert record.outcome is MutationOutcome.FAILURE
    assert record.error == "smtp 550"


async def test_replay_within_a_run_sends_once_and_returns_original_id() -> None:
    email = CountingEmail()
    deduped = IdempotentEmail(email, InMemoryStore())
    with audit_scope(_run()):
        first = await deduped.send(_message(message_id="m-1"))
        # A rebuilt Message (fresh id/timestamp) is still the same logical send.
        again = await deduped.send(
            _message(message_id="m-2").model_copy(
                update={"created_at": datetime(2026, 7, 5, tzinfo=UTC)}
            )
        )
    assert email.sends == 1
    assert first == again == "provider-1"


async def test_pending_replay_refuses_to_resend() -> None:
    email = CountingEmail()
    store = InMemoryStore()
    deduped = IdempotentEmail(email, store)
    with audit_scope(_run()):
        with pytest.raises(RuntimeError, match="smtp 550"):
            await IdempotentEmail(CountingEmail(fail=True), store).send(_message())
        with pytest.raises(ReplayInProgressError, match="send_email"):
            await deduped.send(_message())
    assert email.sends == 0  # at-most-once: the retry did not double-email


async def test_deduped_replay_writes_no_second_mutation_record() -> None:
    email = CountingEmail()
    audit = CollectingAuditLog()
    seam = IdempotentEmail(RecordingEmail(email, audit), InMemoryStore())
    with audit_scope(_run()):
        await seam.send(_message())
        await seam.send(_message())
    assert email.sends == 1
    assert len(audit.records) == 1
