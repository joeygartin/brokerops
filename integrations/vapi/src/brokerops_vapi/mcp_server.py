"""MCP server exposing the voice tools: start_outbound_call, get_call.

Runs standalone over stdio (`uv run mcp-server-vapi`); points at any
Vapi-shaped endpoint via VAPI_BASE_URL (real API by default, stub in demo).
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from brokerops_vapi.adapter import VAPI_API_BASE, VapiVoiceAdapter

mcp = FastMCP("vapi")


def _adapter() -> VapiVoiceAdapter:
    return VapiVoiceAdapter(
        api_key=os.environ.get("VAPI_API_KEY", ""),
        base_url=os.environ.get("VAPI_BASE_URL", VAPI_API_BASE),
    )


async def start_outbound_call(
    contact_id: str,
    assistant_id: str,
    listing_key: str = "",
    phone: str = "",
) -> str:
    """Place an outbound voice call to a contact; returns the call id."""
    context: dict[str, Any] = {"listing_key": listing_key}
    if phone:
        context["phone"] = phone
    return await _adapter().start_outbound_call(contact_id, assistant_id, context)


async def get_call(call_id: str) -> dict[str, Any] | None:
    """Fetch a call record (transcript + outcome) by call id."""
    record = await _adapter().get_call(call_id)
    return record.model_dump(mode="json") if record else None


mcp.tool()(start_outbound_call)
mcp.tool()(get_call)


def main() -> None:
    mcp.run()
