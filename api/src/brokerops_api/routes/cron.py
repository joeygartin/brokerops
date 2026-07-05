"""Scheduled triggers.

In GCP, Cloud Scheduler invokes /internal/cron/milestones with an OIDC
identity token and Cloud Run enforces the audience — no shared secret. For
local/demo use, an optional CRON_SECRET env var gates the endpoint via the
X-Cron-Key header; unset means open (demo mode).
"""

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from brokerops_api.deps import get_approval_repo, get_transaction_store, get_workflow_engine
from brokerops_api.db import ApprovalRepo
from brokerops_api.workflows import TRANSACTION_COORDINATION, WorkflowEngine
from brokerops_core.models.approval import ApprovalStatus
from brokerops_core.ports.transactions import TransactionStore

router = APIRouter(prefix="/internal/cron", tags=["cron"])

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]
StoreDep = Annotated[TransactionStore, Depends(get_transaction_store)]
RepoDep = Annotated[ApprovalRepo, Depends(get_approval_repo)]


@router.post("/milestones")
async def run_milestone_checks(
    engine: EngineDep,
    store: StoreDep,
    repo: RepoDep,
    x_cron_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    secret = os.environ.get("CRON_SECRET")
    if secret and x_cron_key != secret:
        raise HTTPException(status_code=401, detail="bad or missing X-Cron-Key")

    # Don't stack duplicate gates: skip transactions that already have a
    # pending approval in the inbox — an escalation gate or a drafted
    # reminder-email gate (BOP-019); the vapi follow-up gates carry no
    # transaction_id and never match here.
    pending = await repo.list(ApprovalStatus.PENDING)
    awaiting = {
        str(approval.payload.get("transaction_id"))
        for approval in pending
        if approval.kind in ("approve_escalation", "approve_outbound_message")
        and approval.payload.get("transaction_id")
    }

    results: list[dict[str, Any]] = []
    skipped = 0
    for transaction in await store.list_active_transactions():
        if transaction.id in awaiting:
            skipped += 1
            continue
        run = await engine.start(TRANSACTION_COORDINATION, {"transaction_id": transaction.id})
        results.append(
            {
                "transaction_id": transaction.id,
                "status": run.status,
                "outcome": (run.output or {}).get("outcome"),
                "approval_id": run.approval.id if run.approval else None,
            }
        )
    return {"checked": len(results), "skipped_pending_escalation": skipped, "results": results}
