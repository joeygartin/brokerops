import httpx
import pytest

from brokerops_twilio_sms import mcp_server
from brokerops_twilio_sms.adapter import TwilioSMSAdapter
from brokerops_twilio_sms.stub import create_stub_app


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://sms.test")
    adapter = TwilioSMSAdapter(
        account_sid="ACstub00000000000000000000000000",
        auth_token="stub-token",
        from_number="+15005550006",
        base_url="http://sms.test",
        client=client,
    )
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {"send_sms"}


async def test_send_sms_round_trip() -> None:
    provider_id = await mcp_server.send_sms(
        "+15551230101",
        "Hi Sam, thanks for touring!",
        contact_id="101",
        listing_key="RM1001",
    )
    assert provider_id.startswith("SM")
