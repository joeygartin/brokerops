"""Scheduled triggers.

In GCP, Cloud Scheduler invokes /internal/cron/milestones with an OIDC
identity token and Cloud Run enforces the audience — no shared secret. For
local/demo use, an optional CRON_SECRET env var gates the endpoint via the
X-Cron-Key header; unset means open (demo mode).
"""

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from brokerops_api.db import ApprovalRepo, CronRunStore
from brokerops_api.deps import (
    get_approval_repo,
    get_cron_run_store,
    get_transaction_store,
    get_workflow_engine,
)
from brokerops_api.status import MILESTONE_CRON_JOB
from brokerops_api.workflows import TRANSACTION_COORDINATION, WorkflowEngine
from brokerops_core.models.approval import ApprovalStatus
from brokerops_core.ports.transactions import TransactionStore

router = APIRouter(prefix="/internal/cron", tags=["cron"])

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]
StoreDep = Annotated[TransactionStore, Depends(get_transaction_store)]
RepoDep = Annotated[ApprovalRepo, Depends(get_approval_repo)]
CronStoreDep = Annotated[CronRunStore, Depends(get_cron_run_store)]


@router.post("/milestones")
async def run_milestone_checks(
    engine: EngineDep,
    store: StoreDep,
    repo: RepoDep,
    cron_runs: CronStoreDep,
    x_cron_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    secret = os.environ.get("CRON_SECRET")
    if secret and x_cron_key != secret:
        raise HTTPException(status_code=401, detail="bad or missing X-Cron-Key")

    # Don't stack duplicate gates — suppression is PER GATE KIND (BOP-019 review):
    # a pending escalation gate skips the transaction's run entirely (rerunning
    # would raise a second escalation for the same overdue milestone), while a
    # pending drafted-email gate suppresses only the drafted-email tail — the run
    # still executes, so a newly-overdue milestone escalates even while an email
    # draft sits undecided in the inbox. The vapi follow-up gates carry no
    # transaction_id and never match here.
    # Counts live outside the try so a mid-run failure still records how far
    # the pass got (BOP-035: outcome + counts on both success and failure).
    results: list[dict[str, Any]] = []
    skipped_escalation = 0
    email_tail_suppressed = 0
    try:
        pending = await repo.list(ApprovalStatus.PENDING)

        def _awaiting(kind: str) -> set[str]:
            return {
                str((approval.payload or {}).get("transaction_id"))
                for approval in pending
                if approval.kind == kind and (approval.payload or {}).get("transaction_id")
            }

        awaiting_escalation = _awaiting("approve_escalation")
        awaiting_email = _awaiting("approve_outbound_message")

        for transaction in await store.list_active_transactions():
            if transaction.id in awaiting_escalation:
                skipped_escalation += 1
                continue
            suppress_email = transaction.id in awaiting_email
            if suppress_email:
                email_tail_suppressed += 1
            run = await engine.start(
                TRANSACTION_COORDINATION,
                {
                    "transaction_id": transaction.id,
                    "suppress_reminder_email": suppress_email,
                },
            )
            results.append(
                {
                    "transaction_id": transaction.id,
                    "status": run.status,
                    "outcome": (run.output or {}).get("outcome"),
                    "approval_id": run.approval.id if run.approval else None,
                }
            )
        checked = len(results)
        # Persist the outcome so /statusz (and deploy-side alerting) can detect
        # "milestones haven't run in 24h" (BOP-035).
        await cron_runs.record(
            MILESTONE_CRON_JOB,
            outcome="success",
            checked=checked,
            skipped_pending_escalation=skipped_escalation,
            email_tail_suppressed=email_tail_suppressed,
        )
        return {
            "checked": checked,
            "skipped_pending_escalation": skipped_escalation,
            "email_tail_suppressed": email_tail_suppressed,
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as exc:
        from brokerops_api.status import safe_error_label

        await cron_runs.record(
            MILESTONE_CRON_JOB,
            outcome="failure",
            checked=len(results),
            skipped_pending_escalation=skipped_escalation,
            email_tail_suppressed=email_tail_suppressed,
            # Exception class only — never raw message (may carry DSN/key shapes).
            error=safe_error_label(exc),
        )
        raise
