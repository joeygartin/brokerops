from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brokerops_api.deps import get_approval_repo, get_workflow_engine
from brokerops_api.db import ApprovalRepo
from brokerops_api.workflows import WorkflowEngine, WorkflowRunResult
from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest, ApprovalStatus

router = APIRouter(prefix="/approvals", tags=["approvals"])

RepoDep = Annotated[ApprovalRepo, Depends(get_approval_repo)]
EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]


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
    approval_id: str, decision: ApprovalDecision, repo: RepoDep, engine: EngineDep
) -> DecisionResponse:
    if decision.decision is ApprovalStatus.PENDING:
        raise HTTPException(status_code=422, detail="decision must be approved or rejected")
    approval = await repo.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id!r} not found")
    if approval.status is not ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status.value}")
    workflow = await engine.decide(approval, decision)
    decided = await repo.get(approval_id)
    assert decided is not None
    return DecisionResponse(approval=decided, workflow=workflow)
