"""The workflow-run contract shared by every orchestration engine.

Engines (LangGraph, ADK) differ in how they run and pause a workflow, but
they all speak this vocabulary: the same workflow names, the same
WorkflowRunResult shape, and the same rule for reading a finished run's
terminal state. Keeping it here keeps the engines interchangeable.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from brokerops_core.models.approval import ApprovalRequest

LISTING_TO_CONTRACT = "listing_to_contract"
TRANSACTION_COORDINATION = "transaction_coordination"
VAPI_FOLLOWUP = "vapi_followup"

# State channels surfaced to API callers when a run finishes.
OUTPUT_KEYS = (
    "planned_tasks",
    "fub_task_ids",
    "outcome",
    "reminder_task_ids",
    "escalated_task_ids",
    "planned_calls",
    "feedback_id",
    "note_id",
    "call_log_id",
    "hot_task_id",
    "reminder_message_id",
    "followup_message_id",
)
# Stage/outcome values that mean "the run did what it set out to do".
COMPLETED_VALUES = {
    "published",
    "on_track",
    "reminders_sent",
    "escalated",
    "call_queued",
    "synced",
    "agent_notified",
    "reminder_email_sent",
    "followup_sent",
}


class WorkflowRunResult(BaseModel):
    thread_id: str
    status: str
    approval: ApprovalRequest | None = None
    output: dict[str, Any] | None = None


def terminal_run_result(thread_id: str, state: Mapping[str, Any]) -> WorkflowRunResult:
    """Translate a finished run's terminal state into the API-facing result."""
    terminal = state.get("stage") or state.get("outcome")
    terminal_value = str(getattr(terminal, "value", terminal))
    status = "completed" if terminal_value in COMPLETED_VALUES else terminal_value
    output = {key: state[key] for key in OUTPUT_KEYS if key in state} or None
    return WorkflowRunResult(thread_id=thread_id, status=status, output=output)
