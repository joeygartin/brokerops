"""MCP server exposing the outbound SMS tool: send_sms.

Runs standalone over stdio (`uv run mcp-server-twilio-sms`); points at any
Twilio-shaped endpoint via SMS_BASE_URL (real API by default, stub in demo).
Note this is the *raw* provider boundary — sends through the API flow
additionally through the audit/idempotency/tenant seam.
"""

import os
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.message import Message, MessageChannel
from brokerops_twilio_sms.adapter import TWILIO_API_BASE, TwilioSMSAdapter

mcp = FastMCP("twilio-sms")


def _adapter() -> TwilioSMSAdapter:
    return TwilioSMSAdapter(
        account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""),
        from_number=os.environ.get("TWILIO_FROM_NUMBER", ""),
        messaging_service_sid=os.environ.get("TWILIO_MESSAGING_SERVICE_SID", ""),
        base_url=os.environ.get("SMS_BASE_URL", TWILIO_API_BASE),
    )


async def send_sms(
    recipient: str,
    body: str,
    contact_id: str = "",
    listing_key: str = "",
) -> str:
    """Send an outbound SMS to a phone number; returns the provider message id."""
    message = Message(
        id=uuid4().hex,
        channel=MessageChannel.SMS,
        recipient=recipient,
        body=body,
        contact_id=contact_id,
        listing_key=listing_key,
    )
    return await _adapter().send(message)


mcp.tool()(send_sms)


def main() -> None:
    mcp.run()
