from typing import Annotated

from fastapi import APIRouter, Depends, Query

from brokerops_api.deps import get_audit_log, get_current_principal
from brokerops_core.ports.audit import TransactionAuditLog
from brokerops_core.ports.identity import Principal
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.services.egress import scrub_payload

router = APIRouter(prefix="/audit", tags=["audit"])

AuditDep = Annotated[TransactionAuditLog, Depends(get_audit_log)]
# Reads are open to any authenticated role; the response is filtered to the caller's
# role (BOP-027) so a viewer-open surface never receives the raw args/error payload.
ReaderDep = Annotated[Principal, Depends(get_current_principal)]


@router.get("")
async def list_mutations(
    audit: AuditDep,
    principal: ReaderDep,
    workflow_run_id: Annotated[str | None, Query()] = None,
    transaction_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[MutationRecord]:
    """The action audit-ledger: external writes this system performed, newest first.

    Read-only and open to any authenticated operator (viewer and up) — the trail is
    a review surface, not a privileged action. Filter to one run with
    `workflow_run_id`, or to one deal with `transaction_id` for the transaction hub
    (BOP-027) — every write from a transaction-scoped run is stamped with the deal
    id, so that slice is a complete, direct match (no approval-derived guesswork).

    The response is role-filtered: the argument snapshot and error text are redacted
    for viewers, so a viewer-open hub receives the action metadata (tool, integration,
    outcome, actor, time) but never the raw mutation payload.
    """
    if transaction_id is not None:
        records = await audit.list_for_transaction(transaction_id, limit)
    else:
        records = await audit.list(workflow_run_id=workflow_run_id, limit=limit)
    scrubbed: list[MutationRecord] = scrub_payload(records, recipient_role=principal.role)
    return scrubbed
