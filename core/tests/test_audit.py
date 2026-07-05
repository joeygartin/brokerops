"""Unit coverage for the audit seam: redaction and the recording port wrappers.

Engine-independent — the wrappers carry all the recording logic, so it holds below
the engine by construction (architecture rule #5).
"""

from datetime import date
from typing import Any

import pytest

from brokerops_core.models.contact import Contact, ContactCreate, CrmTask
from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.services.audit import (
    AuditContext,
    RecordingCRM,
    RecordingVoice,
    audit_scope,
    redact,
)


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


class FakeCRM:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.created: list[str] = []

    async def get_contact(self, contact_id: str) -> Contact | None:
        return None

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        return []

    async def create_contact(self, draft: ContactCreate) -> Contact:
        return Contact(crm_id="c-1", name=f"{draft.first_name} {draft.last_name}")

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        return "note-1"

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask:
        if self.fail:
            raise RuntimeError("FUB rejected the task")
        self.created.append(name)
        return CrmTask(id="task-1", name=name, due_date=due_date)

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        return "call-1"


class FakeVoice:
    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        return "vapi-1"

    async def get_call(self, call_id: str) -> None:
        return None


def test_redact_masks_secret_keys_deeply() -> None:
    cleaned = redact(
        {
            "contact_id": "42",
            "api_key": "sk-secret",
            "nested": {"Authorization": "Bearer abc", "name": "ok"},
            "list": [{"password": "p"}],
        }
    )
    assert cleaned["contact_id"] == "42"
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["nested"]["Authorization"] == "[redacted]"
    assert cleaned["nested"]["name"] == "ok"
    assert cleaned["list"][0]["password"] == "[redacted]"


async def test_records_successful_write_with_run_context() -> None:
    audit = CollectingAuditLog()
    crm = RecordingCRM(FakeCRM(), audit, integration="fake-crm")
    context = AuditContext(
        workflow_run_id="run-7", workflow="listing_to_contract", approval_id="ap-3", actor="joey@x"
    )
    with audit_scope(context):
        task = await crm.create_task("Order signage", due_date=date(2026, 7, 1))

    assert task.id == "task-1"
    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.tool == "create_task"
    assert record.integration == "fake-crm"
    assert record.outcome is MutationOutcome.SUCCESS
    assert record.external_id == "task-1"
    assert record.workflow_run_id == "run-7"
    assert record.approval_id == "ap-3"
    assert record.actor == "joey@x"
    assert record.args["name"] == "Order signage"
    assert record.error is None


async def test_records_failure_and_reraises() -> None:
    audit = CollectingAuditLog()
    crm = RecordingCRM(FakeCRM(fail=True), audit, integration="fake-crm")
    with audit_scope(AuditContext(workflow_run_id="run-9", workflow="listing_to_contract")):
        with pytest.raises(RuntimeError, match="FUB rejected"):
            await crm.create_task("Order signage", due_date=date(2026, 7, 1))

    assert len(audit.records) == 1
    record = audit.records[0]
    assert record.outcome is MutationOutcome.FAILURE
    assert record.external_id is None
    assert "FUB rejected" in (record.error or "")


async def test_reads_are_not_recorded() -> None:
    audit = CollectingAuditLog()
    crm = RecordingCRM(FakeCRM(), audit, integration="fake-crm")
    with audit_scope(AuditContext(workflow_run_id="run-1", workflow="x")):
        await crm.get_contact("c-1")
        await crm.search_contacts("q")
    assert audit.records == []


async def test_voice_call_is_recorded() -> None:
    audit = CollectingAuditLog()
    voice = RecordingVoice(FakeVoice(), audit)
    with audit_scope(AuditContext(workflow_run_id="run-2", workflow="vapi_followup")):
        call_id = await voice.start_outbound_call("c-1", "assistant-9", {"name": "Sam"})
    assert call_id == "vapi-1"
    assert audit.records[0].tool == "start_outbound_call"
    assert audit.records[0].integration == "vapi"
    assert audit.records[0].external_id == "vapi-1"


async def test_write_outside_a_run_records_with_empty_context() -> None:
    audit = CollectingAuditLog()
    crm = RecordingCRM(FakeCRM(), audit, integration="fake-crm")
    await crm.create_task("Loose write", due_date=date(2026, 7, 1))
    assert audit.records[0].workflow_run_id == ""
    assert audit.records[0].approval_id is None
