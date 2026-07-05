import httpx
import pytest

from brokerops_sierra_crm import mcp_server
from brokerops_sierra_crm.adapter import SierraCRMAdapter
from brokerops_sierra_crm.mcp_server import _adapter as build_env_adapter
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


# `build_env_adapter` is the real env-driven builder, captured at import time
# (the autouse fixture above replaces mcp_server._adapter for the tool tests).


def _clear_sierra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SIERRA_BASE_URL",
        "SIERRA_API_KEY",
        "SIERRA_TASK_ASSIGNEE_ID",
        "SIERRA_TASK_ANCHOR_LEAD_ID",
    ):
        monkeypatch.delenv(var, raising=False)


def test_real_api_base_url_refuses_stub_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # With SIERRA_BASE_URL unset the server points at the REAL Sierra host;
    # falling back to the stub's assignee/anchor ids there would silently
    # create tasks on whatever lead those ids name in a real account.
    _clear_sierra_env(monkeypatch)
    with pytest.raises(RuntimeError, match="SIERRA_API_KEY"):
        build_env_adapter()


def test_real_api_requires_task_config_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sierra_env(monkeypatch)
    monkeypatch.setenv("SIERRA_API_KEY", "real-key")
    with pytest.raises(RuntimeError, match="SIERRA_TASK_ASSIGNEE_ID"):
        build_env_adapter()


def test_real_api_placeholder_values_also_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    # "unset" is the Terraform placeholder for a secret that was never pushed.
    _clear_sierra_env(monkeypatch)
    monkeypatch.setenv("SIERRA_API_KEY", "unset")
    with pytest.raises(RuntimeError, match="SIERRA_API_KEY"):
        build_env_adapter()


def test_stub_base_url_keeps_stub_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pointing at a stub endpoint (anything but the real host) needs no env:
    # the stub's seeded assignee/anchor are safe defaults there and only there.
    _clear_sierra_env(monkeypatch)
    monkeypatch.setenv("SIERRA_BASE_URL", "http://localhost:8004")
    assert isinstance(build_env_adapter(), SierraCRMAdapter)


def test_real_host_url_variants_still_take_the_real_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BOP-037: a trailing-slash or case variant of the real host must not
    # silently select the stub branch, whose seeded assignee/anchor ids would
    # write tasks onto whatever lead they name in a real account.
    _clear_sierra_env(monkeypatch)
    for variant in (
        "https://api.sierrainteractivedev.com/",
        "HTTPS://API.SIERRAINTERACTIVEDEV.COM",
        " https://api.sierrainteractivedev.com ",
    ):
        monkeypatch.setenv("SIERRA_BASE_URL", variant)
        with pytest.raises(RuntimeError, match="SIERRA_API_KEY"):
            build_env_adapter()


def test_adapter_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    # BOP-037: a long-lived MCP server must not leak one unclosed AsyncClient
    # per tool call — the same config yields the same adapter instance.
    _clear_sierra_env(monkeypatch)
    monkeypatch.setenv("SIERRA_BASE_URL", "http://sierra-cache.test")
    assert build_env_adapter() is build_env_adapter()
