"""The workflow-engine seam the API depends on.

Routes and deps type against the WorkflowEngine protocol only; concrete
engines live with their frameworks (brokerops_langgraph.engine, and the ADK
counterpart) and are selected at startup. The shared vocabulary —
workflow names, WorkflowRunResult — lives framework-free in core.
"""

from typing import Any, Protocol

from brokerops_core.models.approval import ApprovalDecision, ApprovalRequest
from brokerops_core.services.workflow_runs import (
    LISTING_TO_CONTRACT,
    TRANSACTION_COORDINATION,
    VAPI_FOLLOWUP,
    WorkflowRunResult,
)

__all__ = [
    "LISTING_TO_CONTRACT",
    "TRANSACTION_COORDINATION",
    "VAPI_FOLLOWUP",
    "WorkflowEngine",
    "WorkflowRunResult",
]


class WorkflowEngine(Protocol):
    async def start(self, workflow: str, run_input: dict[str, Any]) -> WorkflowRunResult: ...

    async def decide(
        self, approval: ApprovalRequest, decision: ApprovalDecision
    ) -> WorkflowRunResult: ...
