"""listing_to_contract — ADK port: intake → draft → [HITL approve] → publish.

Nodes are thin by rule: read state, call a core service or port, write state.
Business rules (eligibility, drafting, task fan-out) live in core services.
The approval gate yields a RequestInput and reruns on resume with the
decision available in ctx.resume_inputs — ADK's interrupt/resume shape.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.workflow import START, FunctionNode, Workflow

from brokerops_adk.interrupts import request_input
from brokerops_core.models.marketing import MarketingDraft
from brokerops_core.models.workflow_state import (
    ApprovalOutcome,
    ListingToContractState,
    WorkflowStage,
)
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.mls import MLSPort
from brokerops_core.services.followup_rules import is_marketable, plan_marketing_tasks
from brokerops_core.services.marketing import draft_marketing

APPROVE_MARKETING = "approve_marketing"
TASK_DUE_DAYS = 2

# A route value with no matching edge ends the run — the ADK spelling of END.
STOP = "stop"


def build_listing_to_contract(mls: MLSPort, crm: CRMPort) -> Workflow:
    async def intake(ctx: Context, listing_key: str) -> None:
        listing = await mls.get_listing(listing_key)
        if listing is None:
            ctx.state["stage"] = WorkflowStage.NOT_FOUND.value
            ctx.route = STOP
            return
        if not is_marketable(listing):
            ctx.state["stage"] = WorkflowStage.NOT_ELIGIBLE.value
            ctx.route = STOP
            return
        ctx.state["stage"] = WorkflowStage.INTAKE.value
        ctx.route = "eligible"

    async def make_draft(ctx: Context, listing_key: str) -> None:
        listing = await mls.get_listing(listing_key)
        if listing is None:
            ctx.state["stage"] = WorkflowStage.NOT_FOUND.value
            ctx.route = STOP
            return
        ctx.state["draft"] = draft_marketing(listing).model_dump(mode="json")
        ctx.state["stage"] = WorkflowStage.DRAFTED.value
        ctx.route = "drafted"

    async def approve_marketing(
        ctx: Context, listing_key: str, draft: MarketingDraft
    ) -> AsyncGenerator[RequestInput, None]:
        decision: dict[str, Any] | None = ctx.resume_inputs.get(APPROVE_MARKETING)
        if decision is None:
            yield request_input(
                APPROVE_MARKETING,
                payload={
                    "kind": APPROVE_MARKETING,
                    "listing_key": listing_key,
                    "draft": draft.model_dump(mode="json"),
                },
            )
            return
        outcome = ApprovalOutcome.model_validate(decision)
        ctx.state["approval"] = outcome.model_dump(mode="json")
        edited = decision.get("edited_payload")
        if edited and edited.get("draft"):
            edited_draft = MarketingDraft.model_validate(edited["draft"])
            ctx.state["draft"] = edited_draft.model_dump(mode="json")
        ctx.route = "approved" if outcome.decision.value == "approved" else "rejected"

    async def publish_tasks(ctx: Context, listing_key: str, draft: MarketingDraft) -> None:
        listing = await mls.get_listing(listing_key)
        if listing is None:
            ctx.state["stage"] = WorkflowStage.NOT_FOUND.value
            return
        tasks = plan_marketing_tasks(listing, draft)
        due = (datetime.now(UTC) + timedelta(days=TASK_DUE_DAYS)).date()
        task_ids = [(await crm.create_task(name, due_date=due)).id for name in tasks]
        ctx.state["planned_tasks"] = tasks
        ctx.state["fub_task_ids"] = task_ids
        ctx.state["stage"] = WorkflowStage.PUBLISHED.value

    async def handle_rejection(ctx: Context) -> None:
        ctx.state["stage"] = WorkflowStage.REJECTED.value

    n_intake = FunctionNode(func=intake)
    n_make_draft = FunctionNode(func=make_draft)
    n_approve = FunctionNode(func=approve_marketing, rerun_on_resume=True)
    n_publish = FunctionNode(func=publish_tasks)
    n_reject = FunctionNode(func=handle_rejection)

    return Workflow(
        name="listing_to_contract",
        state_schema=ListingToContractState,
        edges=[
            (START, n_intake, {"eligible": n_make_draft}),
            (n_make_draft, {"drafted": n_approve}),
            (n_approve, {"approved": n_publish, "rejected": n_reject}),
        ],
    )
