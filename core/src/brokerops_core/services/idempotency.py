"""The second write-boundary seam: make every external write idempotent.

Agents retry and re-plan by nature, and an orchestrator can re-run a node on resume.
A re-issued write must not produce a duplicate side effect — a second FollowUpBoss
contact/note/task, a duplicate call log, or (worst of all) a second outbound phone
call. This wraps the same write-capable ports the audit-ledger wraps (`RecordingCRM`,
`RecordingVoice`), one decorator out, so both engines inherit dedupe identically with
no engine-specific code (architecture rule #5).

Key convention (rule #3 — derivation lives here, tools don't embed it): a stable
SHA-256 over `(workflow_run_id, tool, semantic-args)`. Two calls that mean the same
thing within one run map to the same key; a genuinely new run gets fresh keys. The
run id comes from the same `audit_scope` ContextVar the ledger publishes, so the two
seams agree on what "this run" is.

Layering matters. The idempotency wrapper sits *outside* the recording wrapper, so a
replay short-circuits before the recording wrapper runs — a deduped replay performs
no side effect and writes **no second mutation record** (the BOP-002 contract).

Guarantee: a write performs its side effect **at most once**. The dominant path —
a completed key — returns the original result. The narrow window where an attempt
claimed the key but died before recording its result (a crash between the external
call returning and the local commit) raises `ReplayInProgressError` rather than
repeat the side effect or fabricate a result. See ADR-0011.
"""

import hashlib
import json
from datetime import date
from typing import Any

from brokerops_core.models.call import CallRecord
from brokerops_core.models.contact import Contact, ContactCreate, CrmTask
from brokerops_core.models.idempotency import ClaimStatus, IdempotencyClaim
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.idempotency import IdempotencyStore
from brokerops_core.ports.voice import VoicePort
from brokerops_core.services.audit import current_audit_context


def idempotency_key(workflow_run_id: str, tool: str, args: dict[str, Any]) -> str:
    """Stable key for one logical write: SHA-256 over (run, tool, semantic args)."""
    payload = json.dumps(
        {"workflow_run_id": workflow_run_id, "tool": tool, "args": args},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReplayInProgressError(RuntimeError):
    """A write replayed while its first attempt is still pending — claimed but not
    completed. We refuse to repeat the external side effect (at-most-once) and surface
    this rather than fabricate a result. The window is the gap between the external
    call returning and the local commit; see ADR-0011 for the trade-off.
    """

    def __init__(self, tool: str, key: str) -> None:
        super().__init__(
            f"{tool} replayed while a prior attempt is still in progress "
            f"(key {key[:12]}…); refusing to repeat the external side effect"
        )
        self.tool = tool
        self.key = key


def _current_run_id() -> str | None:
    context = current_audit_context()
    if context is None or not context.workflow_run_id:
        return None
    return context.workflow_run_id


def _require(result: str | None) -> str:
    if result is None:  # a COMPLETED claim always stores a result; defensive only.
        raise RuntimeError("completed idempotency claim is missing its stored result")
    return result


class _Deduper:
    """Shared claim/record logic for the port wrappers."""

    def __init__(self, store: IdempotencyStore) -> None:
        self._store = store

    async def claim(
        self, tool: str, args: dict[str, Any]
    ) -> tuple[str | None, IdempotencyClaim | None]:
        """Claim the key for this write. Returns (key, claim); (None, None) when there
        is no run context to scope a key to — the write then runs undeduped. Raises
        ReplayInProgressError on a still-pending replay.
        """
        run_id = _current_run_id()
        if run_id is None:
            return None, None
        key = idempotency_key(run_id, tool, args)
        claim = await self._store.begin(key, workflow_run_id=run_id, tool=tool)
        if claim.status is ClaimStatus.PENDING:
            raise ReplayInProgressError(tool, key)
        return key, claim

    async def record(self, key: str | None, result: str) -> None:
        if key is not None:
            await self._store.complete(key, result)


class IdempotentCRM:
    """CRMPort decorator: reads pass straight through; each write claims its key, and
    a completed-key replay returns the original result without re-calling the CRM.
    """

    def __init__(self, inner: CRMPort, store: IdempotencyStore) -> None:
        self._inner = inner
        self._dedupe = _Deduper(store)

    async def get_contact(self, contact_id: str) -> Contact | None:
        return await self._inner.get_contact(contact_id)

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        return await self._inner.search_contacts(query, limit)

    async def create_contact(self, draft: ContactCreate) -> Contact:
        args = {"draft": draft.model_dump(mode="json")}
        key, claim = await self._dedupe.claim("create_contact", args)
        if claim is not None and claim.status is ClaimStatus.COMPLETED:
            return Contact.model_validate_json(_require(claim.result))
        contact = await self._inner.create_contact(draft)
        await self._dedupe.record(key, contact.model_dump_json())
        return contact

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        args = {"contact_id": contact_id, "subject": subject, "body": body}
        key, claim = await self._dedupe.claim("add_note", args)
        if claim is not None and claim.status is ClaimStatus.COMPLETED:
            return _require(claim.result)
        note_id = await self._inner.add_note(contact_id, subject, body)
        await self._dedupe.record(key, note_id)
        return note_id

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask:
        args = {
            "name": name,
            "due_date": due_date.isoformat() if due_date is not None else None,
            "contact_id": contact_id,
        }
        key, claim = await self._dedupe.claim("create_task", args)
        if claim is not None and claim.status is ClaimStatus.COMPLETED:
            return CrmTask.model_validate_json(_require(claim.result))
        task = await self._inner.create_task(name, due_date, contact_id)
        await self._dedupe.record(key, task.model_dump_json())
        return task

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        args = {
            "contact_id": contact_id,
            "outcome": outcome,
            "note": note,
            "duration_seconds": duration_seconds,
        }
        key, claim = await self._dedupe.claim("log_call", args)
        if claim is not None and claim.status is ClaimStatus.COMPLETED:
            return _require(claim.result)
        call_id = await self._inner.log_call(contact_id, outcome, note, duration_seconds)
        await self._dedupe.record(key, call_id)
        return call_id


class IdempotentVoice:
    """VoicePort decorator: dedupes the one external write (placing a call) so a
    retry never dials the same contact twice.
    """

    def __init__(self, inner: VoicePort, store: IdempotencyStore) -> None:
        self._inner = inner
        self._dedupe = _Deduper(store)

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        args = {"contact_id": contact_id, "assistant_id": assistant_id, "context": context}
        key, claim = await self._dedupe.claim("start_outbound_call", args)
        if claim is not None and claim.status is ClaimStatus.COMPLETED:
            return _require(claim.result)
        call_id = await self._inner.start_outbound_call(contact_id, assistant_id, context)
        await self._dedupe.record(key, call_id)
        return call_id

    async def get_call(self, call_id: str) -> CallRecord | None:
        return await self._inner.get_call(call_id)
