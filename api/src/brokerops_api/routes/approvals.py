from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brokerops_api.deps import get_approval_repo, get_workflow_engine, require_role
from brokerops_api.db import ApprovalRepo
from brokerops_api.workflows import WorkflowEngine, WorkflowRunResult
from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest, ApprovalStatus
from brokerops_core.ports.identity import Principal, Role

router = APIRouter(prefix="/approvals", tags=["approvals"])

RepoDep = Annotated[ApprovalRepo, Depends(get_approval_repo)]
EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]
# Deciding a human-in-the-loop approval is the one action with real-world side
# effects (CRM writes, contract steps, outbound calls) — restricted to admins.
AdminDep = Annotated[Principal, Depends(require_role(Role.ADMIN))]


class DecideRequest(BaseModel):
    """Wire shape for a decision. `decided_by` is intentionally absent — the API
    stamps it from the authenticated principal, never the client."""

    decision: ApprovalStatus
    edited_payload: dict[str, Any] | None = None


class DecisionResponse(BaseModel):
    approval: ApprovalRequest
    workflow: WorkflowRunResult


@router.get("")
async def list_approvals(
    repo: RepoDep, status: ApprovalStatus | None = ApprovalStatus.PENDING
) -> list[ApprovalRequest]:
    return await repo.list(status)


@router.get("/{approval_id}")
async def get_approval(approval_id: str, repo: RepoDep) -> ApprovalRequest:
    approval = await repo.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id!r} not found")
    return approval


@router.post("/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    request: DecideRequest,
    repo: RepoDep,
    engine: EngineDep,
    principal: AdminDep,
) -> DecisionResponse:
    if request.decision is ApprovalStatus.PENDING:
        raise HTTPException(status_code=422, detail="decision must be approved or rejected")
    # The authenticated operator is the source of truth for who decided.
    decision = ApprovalDecision(
        decision=request.decision,
        decided_by=principal.email,
        edited_payload=request.edited_payload,
    )
    approval = await repo.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id!r} not found")
    if approval.status is not ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status.value}")
    workflow = await engine.decide(approval, decision)
    decided = await repo.get(approval_id)
    assert decided is not None
    return DecisionResponse(approval=decided, workflow=workflow)
