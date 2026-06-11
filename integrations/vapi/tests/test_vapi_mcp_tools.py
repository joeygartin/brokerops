import httpx
import pytest

from brokerops_vapi import mcp_server
from brokerops_vapi.adapter import VapiVoiceAdapter
from brokerops_vapi.stub import create_stub_app


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://vapi.test",
        headers={"Authorization": "Bearer stub-key"},
    )
    adapter = VapiVoiceAdapter(api_key="stub-key", base_url="http://vapi.test", client=client)
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {"start_outbound_call", "get_call"}


async def test_round_trip_through_tools() -> None:
    call_id = await mcp_server.start_outbound_call(
        "103", "demo-assistant", listing_key="RM1003", phone="+1-555-0103"
    )
    record = await mcp_server.get_call(call_id)
    assert record is not None
    assert record["listing_key"] == "RM1003"
    assert record["transcript"]
