from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel

from brokerops_core.models.sensitivity import RESTRICTED_CONTENT


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
    # The deal this write concerns, when it happened inside a transaction-scoped
    # run (BOP-027). Stamped from the run's AuditContext, so a transaction's whole
    # action history is a direct column match — independent of whether the run
    # ever raised an approval. "" for writes with no transaction (a listing
    # workflow, a security denial). Never caller-supplied.
    transaction_id: str = ""
    tool: str
    integration: str
    # The (secret-redacted) argument snapshot and any failure text — role-restricted
    # at egress (BOP-027): redacted for viewers on route responses so the viewer-open
    # audit surfaces show what ran (tool/integration/outcome) but not the raw payload.
    # `| None` is the honest response contract: a viewer-filtered record returns args
    # as null (the redaction of a non-string field), never a dropped/false shape.
    args: Annotated[dict[str, Any] | None, RESTRICTED_CONTENT]
    approval_id: str | None = None
    actor: str | None = None
    outcome: MutationOutcome
    external_id: str | None = None
    error: Annotated[str | None, RESTRICTED_CONTENT] = None
    created_at: datetime
