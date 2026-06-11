import httpx
import pytest

from brokerops_followupboss import mcp_server
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    adapter = FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=client)
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
    found = await mcp_server.search_contacts("casey")
    assert [c["fub_id"] for c in found] == ["102"]
    task = await mcp_server.create_task("Schedule open house", due_date="2026-06-20")
    assert task["id"]
    assert task["due_date"] == "2026-06-20"
