"""listing_to_contract — V1 slice: intake → draft → [HITL approve] → publish.

Nodes are thin by rule: read state, call a core service or port, write state.
Business rules (eligibility, drafting, task fan-out) live in core services.
An approved draft fans out into real CRM tasks through CRMPort.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from brokerops_core.models.marketing import MarketingDraft
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.mls import MLSPort
from brokerops_core.services.followup_rules import is_marketable, plan_marketing_tasks
from brokerops_core.services.marketing import draft_marketing
from brokerops_langgraph.state import ApprovalOutcome, ListingToContractState, WorkflowStage

APPROVE_MARKETING = "approve_marketing"
TASK_DUE_DAYS = 2


def build_listing_to_contract(
    mls: MLSPort, crm: CRMPort, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[Any, Any, Any, Any]:
    async def intake(state: ListingToContractState) -> dict[str, Any]:
        listing = await mls.get_listing(state.listing_key)
        if listing is None:
            return {"stage": WorkflowStage.NOT_FOUND}
        if not is_marketable(listing):
            return {"stage": WorkflowStage.NOT_ELIGIBLE}
        return {"stage": WorkflowStage.INTAKE}

    async def make_draft(state: ListingToContractState) -> dict[str, Any]:
        listing = await mls.get_listing(state.listing_key)
        if listing is None:
            return {"stage": WorkflowStage.NOT_FOUND}
        return {"draft": draft_marketing(listing), "stage": WorkflowStage.DRAFTED}

    async def approve_marketing(state: ListingToContractState) -> dict[str, Any]:
        assert state.draft is not None
        decision: dict[str, Any] = interrupt(
            {
                "kind": APPROVE_MARKETING,
                "listing_key": state.listing_key,
                "draft": state.draft.model_dump(),
            }
        )
        updates: dict[str, Any] = {"approval": ApprovalOutcome.model_validate(decision)}
        edited = decision.get("edited_payload")
        if edited and edited.get("draft"):
            updates["draft"] = MarketingDraft.model_validate(edited["draft"])
        return updates

    async def publish_tasks(state: ListingToContractState) -> dict[str, Any]:
        assert state.draft is not None
        listing = await mls.get_listing(state.listing_key)
        if listing is None:
            return {"stage": WorkflowStage.NOT_FOUND}
        tasks = plan_marketing_tasks(listing, state.draft)
        due = (datetime.now(UTC) + timedelta(days=TASK_DUE_DAYS)).date()
        task_ids = [(await crm.create_task(name, due_date=due)).id for name in tasks]
        return {
            "planned_tasks": tasks,
            "fub_task_ids": task_ids,
            "stage": WorkflowStage.PUBLISHED,
        }

    async def handle_rejection(state: ListingToContractState) -> dict[str, Any]:
        return {"stage": WorkflowStage.REJECTED}

    def route_after_intake(state: ListingToContractState) -> str:
        if state.stage in (WorkflowStage.NOT_FOUND, WorkflowStage.NOT_ELIGIBLE):
            return END
        return "make_draft"

    def route_after_approval(state: ListingToContractState) -> str:
        assert state.approval is not None
        if state.approval.decision.value == "approved":
            return "publish_tasks"
        return "handle_rejection"

    graph = StateGraph(ListingToContractState)
    graph.add_node("intake", intake)
    graph.add_node("make_draft", make_draft)
    graph.add_node(APPROVE_MARKETING, approve_marketing)
    graph.add_node("publish_tasks", publish_tasks)
    graph.add_node("handle_rejection", handle_rejection)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", route_after_intake, ["make_draft", END])
    graph.add_edge("make_draft", APPROVE_MARKETING)
    graph.add_conditional_edges(
        APPROVE_MARKETING, route_after_approval, ["publish_tasks", "handle_rejection"]
    )
    graph.add_edge("publish_tasks", END)
    graph.add_edge("handle_rejection", END)

    return graph.compile(checkpointer=checkpointer)
