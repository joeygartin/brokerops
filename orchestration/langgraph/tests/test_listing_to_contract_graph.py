from typing import Any
from uuid import uuid4

from conftest import GraphFakeCRM, GraphFakeMLS
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _graph(crm: GraphFakeCRM | None = None) -> Any:
    return build_listing_to_contract(GraphFakeMLS(), crm or GraphFakeCRM(), InMemorySaver())


async def test_run_pauses_at_marketing_approval_with_draft_payload() -> None:
    graph = _graph()
    result = await graph.ainvoke({"listing_key": "RM1001"}, _config(uuid4().hex))
    interrupts = result["__interrupt__"]
    assert len(interrupts) == 1
    payload = interrupts[0].value
    assert payload["kind"] == "approve_marketing"
    assert payload["listing_key"] == "RM1001"
    assert "$489,000" in payload["draft"]["headline"]


async def test_approval_resumes_to_published_with_crm_tasks() -> None:
    crm = GraphFakeCRM()
    graph = _graph(crm)
    config = _config(uuid4().hex)
    await graph.ainvoke({"listing_key": "RM1001"}, config)
    result = await graph.ainvoke(
        Command(resume={"decision": "approved", "decided_by": "test-operator"}), config
    )
    assert result["stage"] == "published"
    assert result["planned_tasks"]
    assert result["approval"].decided_by == "test-operator"
    # every planned task became a CRM task, ids recorded in state
    assert result["fub_task_ids"] == [task.id for task in crm.created_tasks]
    assert [task.name for task in crm.created_tasks] == result["planned_tasks"]
    assert all(task.due_date is not None for task in crm.created_tasks)


async def test_rejection_resumes_to_rejected_without_tasks() -> None:
    crm = GraphFakeCRM()
    graph = _graph(crm)
    config = _config(uuid4().hex)
    await graph.ainvoke({"listing_key": "RM1001"}, config)
    result = await graph.ainvoke(
        Command(resume={"decision": "rejected", "decided_by": "test-operator"}), config
    )
    assert result["stage"] == "rejected"
    # the rejection path never writes the planned_tasks channel and never touches the CRM
    assert result.get("planned_tasks", []) == []
    assert crm.created_tasks == []


async def test_approval_with_edited_draft_publishes_the_edit() -> None:
    graph = _graph()
    config = _config(uuid4().hex)
    first = await graph.ainvoke({"listing_key": "RM1001"}, config)
    draft = first["__interrupt__"][0].value["draft"]
    draft["headline"] = "Open House Saturday — Priced to Move"
    result = await graph.ainvoke(
        Command(
            resume={
                "decision": "approved",
                "decided_by": "test-operator",
                "edited_payload": {"draft": draft},
            }
        ),
        config,
    )
    assert result["stage"] == "published"
    assert result["draft"].headline == "Open House Saturday — Priced to Move"


async def test_unknown_listing_ends_not_found() -> None:
    result = await _graph().ainvoke({"listing_key": "RM9999"}, _config(uuid4().hex))
    assert result["stage"] == "not_found"
    assert "__interrupt__" not in result


async def test_closed_listing_is_not_eligible() -> None:
    result = await _graph().ainvoke({"listing_key": "RM1007"}, _config(uuid4().hex))
    assert result["stage"] == "not_eligible"
    assert "__interrupt__" not in result
