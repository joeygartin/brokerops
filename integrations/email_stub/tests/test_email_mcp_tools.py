import httpx
import pytest

from brokerops_email_stub import mcp_server
from brokerops_email_stub.adapter import StubEmailAdapter
from brokerops_email_stub.stub import create_stub_app


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://email.test")
    adapter = StubEmailAdapter(base_url="http://email.test", client=client)
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {"send_email"}


async def test_send_email_round_trip() -> None:
    provider_id = await mcp_server.send_email(
        "sam@example.com",
        "Following up",
        "Hi Sam, thanks for touring.",
        contact_id="101",
        listing_key="RM1001",
    )
    assert provider_id.startswith("stub-email-")
