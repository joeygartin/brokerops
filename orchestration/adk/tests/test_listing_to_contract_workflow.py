"""Scenario parity with the LangGraph listing_to_contract suite, on ADK."""

from workflow_fixtures import GraphFakeCRM, GraphFakeMLS, final_state, make_engine

from brokerops_adk.workflows.listing_to_contract import build_listing_to_contract
from brokerops_core.models.approval import ApprovalDecision, ApprovalStatus
from brokerops_core.services.workflow_runs import LISTING_TO_CONTRACT


def _decision(decision: ApprovalStatus, **kwargs: object) -> ApprovalDecision:
    return ApprovalDecision.model_validate(
        {"decision": decision, "decided_by": "test-operator", **kwargs}
    )


async def test_run_pauses_at_marketing_approval_with_draft_payload() -> None:
    engine, _ = make_engine(build_listing_to_contract(GraphFakeMLS(), GraphFakeCRM()))
    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"})
    assert run.status == "awaiting_approval"
    assert run.approval is not None
    assert run.approval.kind == "approve_marketing"
    payload = run.approval.payload
    assert payload["listing_key"] == "RM1001"
    assert "$489,000" in payload["draft"]["headline"]


async def test_approval_resumes_to_published_with_crm_tasks() -> None:
    crm = GraphFakeCRM()
    engine, sessions = make_engine(build_listing_to_contract(GraphFakeMLS(), crm))
    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"})
    assert run.approval is not None

    result = await engine.decide(run.approval, _decision(ApprovalStatus.APPROVED))
    assert result.status == "completed"
    state = await final_state(sessions, LISTING_TO_CONTRACT, run.thread_id)
    assert state["stage"] == "published"
    assert state["planned_tasks"]
    assert state["approval"]["decided_by"] == "test-operator"
    # every planned task became a CRM task, ids recorded in state
    assert state["fub_task_ids"] == [task.id for task in crm.created_tasks]
    assert [task.name for task in crm.created_tasks] == state["planned_tasks"]
    assert all(task.due_date is not None for task in crm.created_tasks)
    assert result.output is not None
    assert result.output["fub_task_ids"] == state["fub_task_ids"]


async def test_rejection_resumes_to_rejected_without_tasks() -> None:
    crm = GraphFakeCRM()
    engine, sessions = make_engine(build_listing_to_contract(GraphFakeMLS(), crm))
    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"})
    assert run.approval is not None

    result = await engine.decide(run.approval, _decision(ApprovalStatus.REJECTED))
    assert result.status == "rejected"
    state = await final_state(sessions, LISTING_TO_CONTRACT, run.thread_id)
    assert state["stage"] == "rejected"
    # the rejection path never writes the planned_tasks channel and never touches the CRM
    assert state.get("planned_tasks", []) == []
    assert crm.created_tasks == []


async def test_approval_with_edited_draft_publishes_the_edit() -> None:
    engine, sessions = make_engine(build_listing_to_contract(GraphFakeMLS(), GraphFakeCRM()))
    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1001"})
    assert run.approval is not None
    draft = dict(run.approval.payload["draft"])
    draft["headline"] = "Open House Saturday — Priced to Move"

    result = await engine.decide(
        run.approval,
        _decision(ApprovalStatus.APPROVED, edited_payload={"draft": draft}),
    )
    assert result.status == "completed"
    state = await final_state(sessions, LISTING_TO_CONTRACT, run.thread_id)
    assert state["stage"] == "published"
    assert state["draft"]["headline"] == "Open House Saturday — Priced to Move"


async def test_unknown_listing_ends_not_found() -> None:
    engine, _ = make_engine(build_listing_to_contract(GraphFakeMLS(), GraphFakeCRM()))
    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM9999"})
    assert run.status == "not_found"
    assert run.approval is None


async def test_closed_listing_is_not_eligible() -> None:
    engine, _ = make_engine(build_listing_to_contract(GraphFakeMLS(), GraphFakeCRM()))
    run = await engine.start(LISTING_TO_CONTRACT, {"listing_key": "RM1007"})
    assert run.status == "not_eligible"
    assert run.approval is None
