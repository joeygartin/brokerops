"""MCP tool tests — the three MLS tools against the in-process mock."""

import httpx
import pytest

from brokerops_mls_reso import mcp_server
from brokerops_mls_reso.adapter import ResoMLSAdapter
from brokerops_mls_reso.server import create_app


@pytest.fixture(autouse=True)
def in_process_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.ASGITransport(app=create_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://mls.test/odata")
    adapter = ResoMLSAdapter(base_url="http://mls.test", client=client)
    monkeypatch.setattr(mcp_server, "_adapter", lambda: adapter)


def test_tools_are_registered() -> None:
    registered = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert registered == {"search_listings", "get_listing", "get_listing_media"}


async def test_search_listings_tool() -> None:
    results = await mcp_server.search_listings(status="active", min_bedrooms=5)
    assert {r["mls_id"] for r in results} == {"RM1006", "RM1009"}
    assert all(r["status"] == "active" for r in results)


async def test_get_listing_tool() -> None:
    listing = await mcp_server.get_listing("RM1001")
    assert listing is not None
    assert listing["list_price"] == 489000
    assert await mcp_server.get_listing("RM9999") is None


async def test_get_listing_media_tool() -> None:
    media = await mcp_server.get_listing_media("RM1001")
    assert [m["media_key"] for m in media] == ["RM1001-M1", "RM1001-M2", "RM1001-M3"]
