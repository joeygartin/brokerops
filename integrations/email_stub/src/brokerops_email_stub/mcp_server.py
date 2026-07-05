"""MCP server exposing the outbound business-email tool: send_email.

Runs standalone over stdio (`uv run mcp-server-email-stub`); points at any
stub-shaped endpoint via EMAIL_BASE_URL. Note this is the *raw* provider boundary —
sends through the API flow additionally through the audit/idempotency/tenant seam.
"""

import os
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.message import Message, MessageChannel
from brokerops_email_stub.adapter import EMAIL_STUB_BASE, StubEmailAdapter

mcp = FastMCP("email")


def _adapter() -> StubEmailAdapter:
    return StubEmailAdapter(base_url=os.environ.get("EMAIL_BASE_URL", EMAIL_STUB_BASE))


async def send_email(
    recipient: str,
    subject: str,
    body: str,
    contact_id: str = "",
    listing_key: str = "",
) -> str:
    """Send an outbound email to a recipient; returns the provider message id."""
    message = Message(
        id=uuid4().hex,
        channel=MessageChannel.EMAIL,
        recipient=recipient,
        subject=subject,
        body=body,
        contact_id=contact_id,
        listing_key=listing_key,
    )
    return await _adapter().send(message)


mcp.tool()(send_email)


def main() -> None:
    mcp.run()
