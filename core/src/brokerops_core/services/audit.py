"""The single write-boundary seam for the action audit-ledger.

Both orchestration engines reach external systems through the same ports, wired
once in the API. Wrapping the write-capable ports here — `RecordingCRM`,
`RecordingVoice`, `RecordingEmail` — records every external mutation in one place, so the two
engines behave identically with no engine-specific code (architecture rule #5).
The ADK `before_tool_callback`/LangGraph node-wrapper that the task sketched only
fire for LlmAgent tool calls; these workflows are deterministic zero-LLM
FunctionNodes that call the ports directly, so a port decorator is the genuinely
single seam.

Per-run context (which run, which approval, which actor) can't be passed through
the long-lived port objects, so each engine publishes it on a ContextVar at its
run boundary via `audit_scope`; the wrappers read it when they emit. The ContextVar
is copied into any child task the engine spawns, so writes inside a run always see
their run's context.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from brokerops_core.models.call import CallRecord
from brokerops_core.models.contact import Contact, ContactCreate, CrmTask
from brokerops_core.models.message import Message
from brokerops_core.models.mutation import MutationOutcome, MutationRecord
from brokerops_core.ports.audit import AuditLog
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.messaging import EmailPort
from brokerops_core.ports.voice import VoicePort

# Substrings that mark an argument key as sensitive; matched case-insensitively so
# nothing secret is ever persisted to the trail, even from a future caller.
_SECRET_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "bearer",
    "authorization",
    "credential",
    "private_key",
)
_REDACTED = "[redacted]"


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def redact(value: Any) -> Any:
    """Deep-copy `value`, masking the values of any secret-looking keys."""
    if isinstance(value, dict):
        return {k: (_REDACTED if _is_secret(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class AuditContext:
    """The run-scoped facts a write should be attributed to."""

    workflow_run_id: str
    workflow: str
    approval_id: str | None = None
    actor: str | None = None


_audit_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


@contextmanager
def audit_scope(context: AuditContext) -> Iterator[None]:
    """Publish `context` for the duration of a workflow run; reset on exit."""
    token = _audit_context.set(context)
    try:
        yield
    finally:
        _audit_context.reset(token)


def current_audit_context() -> AuditContext | None:
    return _audit_context.get()


class _Recorder:
    """Shared emit logic for the port wrappers."""

    def __init__(self, audit: AuditLog, integration: str) -> None:
        self._audit = audit
        self._integration = integration

    async def emit(
        self,
        tool: str,
        args: dict[str, Any],
        outcome: MutationOutcome,
        *,
        external_id: str | None = None,
        error: str | None = None,
    ) -> None:
        context = current_audit_context()
        record = MutationRecord(
            id=uuid4().hex,
            workflow_run_id=context.workflow_run_id if context else "",
            workflow=context.workflow if context else "",
            tool=tool,
            integration=self._integration,
            args=redact(args),
            approval_id=context.approval_id if context else None,
            actor=context.actor if context else None,
            outcome=outcome,
            external_id=external_id,
            error=error,
            created_at=datetime.now(UTC),
        )
        await self._audit.record(record)


class RecordingCRM:
    """CRMPort decorator: reads pass straight through; every write is recorded
    once — success or failure — before the result is returned or the error re-raised.
    """

    def __init__(self, inner: CRMPort, audit: AuditLog) -> None:
        self._inner = inner
        self._rec = _Recorder(audit, "followupboss")

    async def get_contact(self, contact_id: str) -> Contact | None:
        return await self._inner.get_contact(contact_id)

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        return await self._inner.search_contacts(query, limit)

    async def create_contact(self, draft: ContactCreate) -> Contact:
        args = {"draft": draft.model_dump(mode="json")}
        try:
            result = await self._inner.create_contact(draft)
        except Exception as exc:
            await self._rec.emit("create_contact", args, MutationOutcome.FAILURE, error=str(exc))
            raise
        await self._rec.emit(
            "create_contact", args, MutationOutcome.SUCCESS, external_id=result.fub_id
        )
        return result

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        args = {"contact_id": contact_id, "subject": subject, "body": body}
        try:
            note_id = await self._inner.add_note(contact_id, subject, body)
        except Exception as exc:
            await self._rec.emit("add_note", args, MutationOutcome.FAILURE, error=str(exc))
            raise
        await self._rec.emit("add_note", args, MutationOutcome.SUCCESS, external_id=note_id)
        return note_id

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask:
        args = {
            "name": name,
            "due_date": due_date.isoformat() if due_date is not None else None,
            "contact_id": contact_id,
        }
        try:
            task = await self._inner.create_task(name, due_date, contact_id)
        except Exception as exc:
            await self._rec.emit("create_task", args, MutationOutcome.FAILURE, error=str(exc))
            raise
        await self._rec.emit("create_task", args, MutationOutcome.SUCCESS, external_id=task.id)
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
        try:
            call_id = await self._inner.log_call(contact_id, outcome, note, duration_seconds)
        except Exception as exc:
            await self._rec.emit("log_call", args, MutationOutcome.FAILURE, error=str(exc))
            raise
        await self._rec.emit("log_call", args, MutationOutcome.SUCCESS, external_id=call_id)
        return call_id


class RecordingVoice:
    """VoicePort decorator: records the one external write (placing a call)."""

    def __init__(self, inner: VoicePort, audit: AuditLog) -> None:
        self._inner = inner
        self._rec = _Recorder(audit, "vapi")

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        args = {"contact_id": contact_id, "assistant_id": assistant_id, "context": context}
        try:
            call_id = await self._inner.start_outbound_call(contact_id, assistant_id, context)
        except Exception as exc:
            await self._rec.emit(
                "start_outbound_call", args, MutationOutcome.FAILURE, error=str(exc)
            )
            raise
        await self._rec.emit(
            "start_outbound_call", args, MutationOutcome.SUCCESS, external_id=call_id
        )
        return call_id

    async def get_call(self, call_id: str) -> CallRecord | None:
        return await self._inner.get_call(call_id)


class RecordingEmail:
    """EmailPort decorator: records the one external write (sending a message).

    Same seam as RecordingCRM/RecordingVoice (ADR-0010). The outcome-bearing fields
    live in the record itself (external_id = the provider message id, error on
    failure), so they are excluded from the argument snapshot.
    """

    def __init__(self, inner: EmailPort, audit: AuditLog) -> None:
        self._inner = inner
        self._rec = _Recorder(audit, "email")

    async def send(self, message: Message) -> str:
        args = message.model_dump(mode="json", exclude={"status", "provider_message_id", "sent_at"})
        try:
            provider_id = await self._inner.send(message)
        except Exception as exc:
            await self._rec.emit("send_email", args, MutationOutcome.FAILURE, error=str(exc))
            raise
        await self._rec.emit("send_email", args, MutationOutcome.SUCCESS, external_id=provider_id)
        return provider_id
