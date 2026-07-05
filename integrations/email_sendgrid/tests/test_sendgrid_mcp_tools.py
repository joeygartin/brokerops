import httpx
import pytest

from brokerops_email_sendgrid import mcp_server
from brokerops_email_sendgrid.adapter import SendGridEmailAdapter
from brokerops_email_sendgrid.stub import STUB_API_KEY, create_stub_app


@pytest.fixture
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_stub_app()),
        base_url="http://sendgrid.test",
        headers={"Authorization": f"Bearer {STUB_API_KEY}"},
    )
    adapter = SendGridEmailAdapter(
        api_key=STUB_API_KEY,
        from_email="updates@brokerage.test",
        base_url="http://sendgrid.test",
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
    assert provider_id.startswith("stub-sg-")


def test_real_api_host_without_a_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fail-loud posture, mirroring the api wiring: against the real host the
    # key and sender identity must be explicit, never a placeholder.
    monkeypatch.delenv("SENDGRID_BASE_URL", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "updates@brokerage.test")
    with pytest.raises(RuntimeError, match="SENDGRID_API_KEY"):
        mcp_server._adapter()


def test_real_api_host_with_a_placeholder_sender_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SENDGRID_BASE_URL", raising=False)
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-sendgrid-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "unset")
    with pytest.raises(RuntimeError, match="SENDGRID_FROM_EMAIL"):
        mcp_server._adapter()


def test_a_stub_base_url_keeps_stub_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENDGRID_BASE_URL", "http://localhost:8026")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
    assert isinstance(mcp_server._adapter(), SendGridEmailAdapter)


REAL_ADAPTER_FACTORY = mcp_server._adapter


def test_real_host_url_variants_still_take_the_real_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BOP-037: a trailing-slash or case variant of the real host must not
    # silently select the stub branch, whose placeholder key and synthetic
    # sender must never be used against api.sendgrid.com.
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "updates@brokerage.test")
    for variant in (
        "https://api.sendgrid.com/",
        "HTTPS://API.SENDGRID.COM",
        " https://api.sendgrid.com ",
    ):
        monkeypatch.setenv("SENDGRID_BASE_URL", variant)
        with pytest.raises(RuntimeError, match="SENDGRID_API_KEY"):
            REAL_ADAPTER_FACTORY()


def test_adapter_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    # BOP-037: a long-lived MCP server must not leak one unclosed AsyncClient
    # per tool call — the same config yields the same adapter instance.
    monkeypatch.setenv("SENDGRID_BASE_URL", "http://sendgrid-cache.test")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
    assert REAL_ADAPTER_FACTORY() is REAL_ADAPTER_FACTORY()
