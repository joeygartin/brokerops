from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class MutationOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


class MutationRecord(BaseModel):
    """One durable, reviewable line in the business audit trail: a single write the
    system performed against an external system across the MCP boundary.

    Distinct from an ApprovalRequest (which records a *decision*) and from tracing
    (which records *execution* for debugging) — this is the trustworthy history of
    the actions themselves: what tool ran, on which integration, with what
    (secret-redacted) arguments, under which workflow run, on whose authority, and
    with what result. Both success and failure are recorded.
    """

    id: str
    workflow_run_id: str
    workflow: str
    tool: str
    integration: str
    args: dict[str, Any]
    approval_id: str | None = None
    actor: str | None = None
    outcome: MutationOutcome
    external_id: str | None = None
    error: str | None = None
    created_at: datetime
