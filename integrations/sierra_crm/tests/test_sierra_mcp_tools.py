import httpx
import pytest

from brokerops_sierra_crm import mcp_server
from brokerops_sierra_crm.adapter import SierraCRMAdapter
from brokerops_sierra_crm.stub import (
    STUB_TASK_ANCHOR_LEAD_ID,
    STUB_TASK_ASSIGNEE_ID,
    create_stub_app,
)


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://sierra.test",
        headers={"Sierra-ApiKey": "stub-key"},
    )
    adapter = SierraCRMAdapter(
        api_key="stub-key",
        base_url="http://sierra.test",
        client=client,
        task_assignee_id=STUB_TASK_ASSIGNEE_ID,
        task_anchor_lead_id=STUB_TASK_ANCHOR_LEAD_ID,
    )
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_all_six_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {
        "get_contact",
        "search_contacts",
        "create_contact",
        "add_note",
        "create_task",
        "log_call",
    }


async def test_search_and_task_tools() -> None:
    found = await mcp_server.search_contacts("priya")
    assert [c["crm_id"] for c in found] == ["502"]
    task = await mcp_server.create_task("Schedule open house", due_date="2026-06-20")
    assert task["id"]
    assert task["due_date"] == "2026-06-20"
