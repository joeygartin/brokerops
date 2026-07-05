import httpx
import pytest

from brokerops_google_drive import mcp_server
from brokerops_google_drive.adapter import GoogleDriveFilesAdapter
from brokerops_google_drive.stub import create_stub_app


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://drive.test")
    adapter = GoogleDriveFilesAdapter(base_url="http://drive.test", client=client)
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {"list_files", "get_file", "put_file"}


async def test_round_trip_through_tools() -> None:
    stored = await mcp_server.put_file("Counter offer.pdf", "synthetic counter", "RM1004")
    listed = await mcp_server.list_files("RM1004")
    assert stored["name"] in {item["name"] for item in listed}
    fetched = await mcp_server.get_file(stored["file_id"])
    assert fetched is not None
    assert fetched["web_url"].endswith(f"/view/{stored['file_id']}")
    assert await mcp_server.get_file("drive-nope") is None
