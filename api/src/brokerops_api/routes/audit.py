from typing import Annotated

from fastapi import APIRouter, Depends, Query

from brokerops_api.deps import get_audit_log
from brokerops_core.ports.audit import AuditLog
from brokerops_core.models.mutation import MutationRecord

router = APIRouter(prefix="/audit", tags=["audit"])

AuditDep = Annotated[AuditLog, Depends(get_audit_log)]


@router.get("")
async def list_mutations(
    audit: AuditDep,
    workflow_run_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[MutationRecord]:
    """The action audit-ledger: external writes this system performed, newest first.

    Read-only and open to any authenticated operator (viewer and up) — the trail is
    a review surface, not a privileged action. Filter to one run with
    `workflow_run_id` to see everything done under a single workflow run.
    """
    return await audit.list(workflow_run_id=workflow_run_id, limit=limit)
