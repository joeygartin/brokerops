from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from brokerops_api.deps import get_approval_repo, get_workflow_engine, require_role
from brokerops_api.db import ApprovalRepo
from brokerops_api.workflows import WorkflowEngine, WorkflowRunResult
from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest, ApprovalStatus
from brokerops_core.models.drafting import EditedMessagePayload
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.services.message_send import UnknownOutboundMessageError

router = APIRouter(prefix="/approvals", tags=["approvals"])

# The drafted-comms gate kind (BOP-019) — the one whose edited_payload has a
# typed boundary shape (EditedMessagePayload). Other kinds keep their own
# payload conventions (e.g. the marketing gate's {"draft": …}).
APPROVE_OUTBOUND_MESSAGE = "approve_outbound_message"

RepoDep = Annotated[ApprovalRepo, Depends(get_approval_repo)]
EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]
# Deciding a human-in-the-loop approval is the one action with real-world side
# effects (CRM writes, contract steps, outbound calls) — restricted to admins.
AdminDep = Annotated[Principal, Depends(require_role(Role.ADMIN))]


class DecideRequest(BaseModel):
    """Wire shape for a decision. `decided_by` is intentionally absent — the API
    stamps it from the authenticated principal, never the client.

    `edited_payload` stays a dict here because its shape depends on the approval
    *kind*, known only after the row is fetched; the decide route validates it
    against the kind's boundary model (EditedMessagePayload for the
    outbound-message gate) before anything reaches an engine."""

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
    approval = await repo.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id!r} not found")
    if approval.status is not ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status.value}")
    # Pydantic at the boundary (BOP-037): the outbound-message gate's edits are
    # validated against their typed shape here, so a hostile payload is a 422 —
    # never a FAILED row + 500 halfway through a provider send.
    if approval.kind == APPROVE_OUTBOUND_MESSAGE and request.edited_payload is not None:
        try:
            EditedMessagePayload.model_validate(request.edited_payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The authenticated operator is the source of truth for who decided.
    decision = ApprovalDecision(
        decision=request.decision,
        decided_by=principal.email,
        edited_payload=request.edited_payload,
    )
    try:
        workflow = await engine.decide(approval, decision)
    except UnknownOutboundMessageError as exc:
        # The gate's message row is gone (BOP-037): a state conflict between the
        # approval and the comms history — a clean 409, not an AssertionError 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    decided = await repo.get(approval_id)
    assert decided is not None
    return DecisionResponse(approval=decided, workflow=workflow)
