import httpx
import pytest

from brokerops_twilio_sms import mcp_server
from brokerops_twilio_sms.adapter import TwilioSMSAdapter
from brokerops_twilio_sms.stub import create_stub_app

# The real config-driven factory, captured at import time — the round-trip tests
# monkeypatch mcp_server._adapter away, but the fail-loud tests below must
# exercise the original.
REAL_ADAPTER_FACTORY = mcp_server._adapter

CONFIG_VARS = (
    "SMS_BASE_URL",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TWILIO_MESSAGING_SERVICE_SID",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in CONFIG_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
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


@pytest.mark.usefixtures("in_process_adapter")
async def test_send_sms_round_trip() -> None:
    provider_id = await mcp_server.send_sms(
        "+15551230101",
        "Hi Sam, thanks for touring!",
        contact_id="101",
        listing_key="RM1001",
    )
    assert provider_id.startswith("SM")


# ── the fail-loud config posture against the real API host (sierra precedent) ──


def test_real_api_with_no_config_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    # SMS_BASE_URL unset defaults to api.twilio.com: stub placeholders must
    # never leak into a request against the real API.
    with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID is required"):
        REAL_ADAPTER_FACTORY()


def test_real_api_with_placeholder_secret_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    # "unset" is the Terraform placeholder for a secret that was never pushed.
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "unset")
    with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN is required"):
        REAL_ADAPTER_FACTORY()


def test_real_api_without_a_sender_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "real-token")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "unset")  # placeholder ≠ a sender
    with pytest.raises(RuntimeError, match="TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID"):
        REAL_ADAPTER_FACTORY()


def test_real_api_fully_configured_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "real-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MGreal0000000000000000000000000000")
    assert isinstance(REAL_ADAPTER_FACTORY(), TwilioSMSAdapter)


def test_non_default_base_url_keeps_permissive_stub_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pointing SMS_BASE_URL at a stub endpoint is the demo posture: no
    # credentials required there, and only there.
    monkeypatch.setenv("SMS_BASE_URL", "http://localhost:8026")
    assert isinstance(REAL_ADAPTER_FACTORY(), TwilioSMSAdapter)
