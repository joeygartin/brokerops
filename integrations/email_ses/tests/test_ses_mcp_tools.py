import httpx
import pytest

from brokerops_email_ses import mcp_server
from brokerops_email_ses.adapter import SESEmailAdapter
from brokerops_email_ses.stub import (
    STUB_ACCESS_KEY_ID,
    STUB_SECRET_ACCESS_KEY,
    create_stub_app,
)


@pytest.fixture
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://ses.test")
    adapter = SESEmailAdapter(
        access_key_id=STUB_ACCESS_KEY_ID,
        secret_access_key=STUB_SECRET_ACCESS_KEY,
        from_address="updates@demo.example.com",
        base_url="http://ses.test",
        client=client,
    )
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {"send_email"}


@pytest.mark.usefixtures("in_process_adapter")
async def test_send_email_round_trip() -> None:
    provider_id = await mcp_server.send_email(
        "sam@example.com",
        "Following up",
        "Hi Sam, thanks for touring.",
        contact_id="101",
        listing_key="RM1001",
    )
    assert provider_id.endswith("-ses-stub")


@pytest.mark.parametrize(
    "missing", ["SES_ACCESS_KEY_ID", "SES_SECRET_ACCESS_KEY", "SES_FROM_ADDRESS"]
)
def test_missing_config_fails_loud(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    # Same posture as the api wiring (ADR-0014/0015): no silent downgrade.
    for name in ("SES_ACCESS_KEY_ID", "SES_SECRET_ACCESS_KEY", "SES_FROM_ADDRESS"):
        monkeypatch.setenv(name, "fake-value")
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(RuntimeError, match=missing):
        mcp_server._adapter()


def test_the_terraform_placeholder_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SES_ACCESS_KEY_ID", "fake-value")
    monkeypatch.setenv("SES_SECRET_ACCESS_KEY", "unset")
    monkeypatch.setenv("SES_FROM_ADDRESS", "fake-value")
    with pytest.raises(RuntimeError, match="SES_SECRET_ACCESS_KEY"):
        mcp_server._adapter()


def test_adapter_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    # BOP-037: a long-lived MCP server must not leak one unclosed AsyncClient
    # per tool call — the same config yields the same adapter instance. (SES has
    # no real-vs-stub branch to normalize; config is fail-loud on every path.)
    for name in ("SES_ACCESS_KEY_ID", "SES_SECRET_ACCESS_KEY", "SES_FROM_ADDRESS"):
        monkeypatch.setenv(name, "fake-cache-value")
    monkeypatch.setenv("SES_BASE_URL", "http://ses-cache.test")
    assert mcp_server._adapter() is mcp_server._adapter()
