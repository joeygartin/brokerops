"""Unit coverage for the idempotency seam: the key convention and the port wrappers.

Engine-independent — the wrappers carry all the dedupe logic, so it holds below the
engine by construction (architecture rule #5), exactly like the audit seam.
"""

from datetime import date
from typing import Any

import pytest

from brokerops_core.models.contact import Contact, ContactCreate, CrmTask
from brokerops_core.models.idempotency import ClaimStatus
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.services.audit import AuditContext, RecordingCRM, audit_scope
from brokerops_core.services.idempotency import (
    IdempotentCRM,
    IdempotentVoice,
    ReplayInProgressError,
    idempotency_key,
)


class InMemoryStore:
    """Mirror of api.db.InMemoryIdempotencyStore, kept here so core tests stay in core."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[ClaimStatus, str | None]] = {}

    async def begin(self, key: str, *, workflow_run_id: str, tool: str):  # type: ignore[no-untyped-def]
        from brokerops_core.models.idempotency import IdempotencyClaim

        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = (ClaimStatus.PENDING, None)
            return IdempotencyClaim(status=ClaimStatus.NEW)
        status, result = existing
        return IdempotencyClaim(status=status, result=result)

    async def complete(self, key: str, result: str) -> None:
        self._rows[key] = (ClaimStatus.COMPLETED, result)


class CountingCRM:
    def __init__(self) -> None:
        self.create_task_calls = 0
        self.add_note_calls = 0
        self.create_contact_calls = 0
        self.get_contact_calls = 0

    async def get_contact(self, contact_id: str) -> Contact | None:
        self.get_contact_calls += 1
        return Contact(crm_id=contact_id, name="Sam")

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        return []

    async def create_contact(self, draft: ContactCreate) -> Contact:
        self.create_contact_calls += 1
        return Contact(crm_id=f"c-{self.create_contact_calls}", name=draft.first_name)

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        self.add_note_calls += 1
        return f"note-{self.add_note_calls}"

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask:
        self.create_task_calls += 1
        return CrmTask(id=f"task-{self.create_task_calls}", name=name, due_date=due_date)

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        return "call-1"


class CountingVoice:
    def __init__(self) -> None:
        self.calls = 0

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        self.calls += 1
        return f"vapi-{self.calls}"

    async def get_call(self, call_id: str) -> None:
        return None


class CollectingAuditLog:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


def _run(run_id: str = "run-1") -> AuditContext:
    return AuditContext(workflow_run_id=run_id, workflow="listing_to_contract")


def test_key_is_stable_and_sensitive() -> None:
    base = idempotency_key("run-1", "create_task", {"name": "Sign"})
    assert base == idempotency_key("run-1", "create_task", {"name": "Sign"})
    assert base != idempotency_key("run-2", "create_task", {"name": "Sign"})
    assert base != idempotency_key("run-1", "add_note", {"name": "Sign"})
    assert base != idempotency_key("run-1", "create_task", {"name": "Photos"})


async def test_replay_within_a_run_skips_side_effect_and_returns_original() -> None:
    crm = CountingCRM()
    deduped = IdempotentCRM(crm, InMemoryStore())
    with audit_scope(_run()):
        first = await deduped.create_task("Order signage", due_date=date(2026, 7, 1))
        again = await deduped.create_task("Order signage", due_date=date(2026, 7, 1))
    assert crm.create_task_calls == 1  # the side effect happened at most once
    assert first.id == again.id == "task-1"  # replay returns the original result
    assert again.due_date == date(2026, 7, 1)


async def test_distinct_args_are_not_deduped() -> None:
    crm = CountingCRM()
    deduped = IdempotentCRM(crm, InMemoryStore())
    with audit_scope(_run()):
        await deduped.create_task("Order signage", due_date=date(2026, 7, 1))
        await deduped.create_task("Schedule photos", due_date=date(2026, 7, 1))
    assert crm.create_task_calls == 2


async def test_different_runs_are_not_deduped() -> None:
    crm = CountingCRM()
    store = InMemoryStore()
    with audit_scope(_run("run-a")):
        await IdempotentCRM(crm, store).create_task("Order signage", due_date=date(2026, 7, 1))
    with audit_scope(_run("run-b")):
        await IdempotentCRM(crm, store).create_task("Order signage", due_date=date(2026, 7, 1))
    assert crm.create_task_calls == 2


async def test_writes_outside_a_run_are_not_deduped() -> None:
    # No audit scope → no run context to key on → the write runs undeduped.
    crm = CountingCRM()
    deduped = IdempotentCRM(crm, InMemoryStore())
    await deduped.create_task("Loose write", due_date=date(2026, 7, 1))
    await deduped.create_task("Loose write", due_date=date(2026, 7, 1))
    assert crm.create_task_calls == 2


async def test_pending_replay_refuses_to_repeat_the_side_effect() -> None:
    # Simulate a mid-write crash: a prior attempt claimed the key but never completed.
    crm = CountingCRM()
    store = InMemoryStore()
    args = {"name": "Order signage", "due_date": "2026-07-01", "contact_id": None}
    key = idempotency_key("run-1", "create_task", args)
    await store.begin(key, workflow_run_id="run-1", tool="create_task")  # claim, never complete
    with audit_scope(_run()):
        with pytest.raises(ReplayInProgressError, match="create_task"):
            await IdempotentCRM(crm, store).create_task("Order signage", due_date=date(2026, 7, 1))
    assert crm.create_task_calls == 0  # at most once: we did not re-run the side effect


async def test_reads_pass_through_undeduped() -> None:
    crm = CountingCRM()
    deduped = IdempotentCRM(crm, InMemoryStore())
    with audit_scope(_run()):
        await deduped.get_contact("c-1")
        await deduped.get_contact("c-1")
    assert crm.get_contact_calls == 2


async def test_voice_call_is_not_placed_twice() -> None:
    voice = CountingVoice()
    deduped = IdempotentVoice(voice, InMemoryStore())
    with audit_scope(AuditContext(workflow_run_id="run-2", workflow="vapi_followup")):
        first = await deduped.start_outbound_call("c-1", "assistant-9", {"name": "Sam"})
        again = await deduped.start_outbound_call("c-1", "assistant-9", {"name": "Sam"})
    assert voice.calls == 1
    assert first == again == "vapi-1"


async def test_deduped_replay_writes_no_second_mutation_record() -> None:
    # Idempotency wraps recording, so a replay short-circuits before the audit
    # ledger — the BOP-002 contract: a deduped replay emits no second record.
    crm = CountingCRM()
    audit = CollectingAuditLog()
    deduped = IdempotentCRM(RecordingCRM(crm, audit, integration="fake-crm"), InMemoryStore())
    with audit_scope(_run()):
        await deduped.create_task("Order signage", due_date=date(2026, 7, 1))
        await deduped.create_task("Order signage", due_date=date(2026, 7, 1))
    assert crm.create_task_calls == 1
    assert len(audit.records) == 1
    assert audit.records[0].tool == "create_task"
