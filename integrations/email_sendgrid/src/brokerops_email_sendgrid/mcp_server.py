"""MCP server exposing the outbound business-email tool over SendGrid: send_email.

Runs standalone over stdio (`uv run mcp-server-email-sendgrid`); points at any
SendGrid-shaped endpoint via SENDGRID_BASE_URL (real API by default, the
bundled stub for offline work). Against the real API host, the API key and the
domain-authenticated sender identity must both be explicit — fail-loud,
mirroring the api wiring (ADR-0015): a missing key must never silently send
nothing, and a placeholder from-address must never reach a client's inbox.
Note this is the *raw* provider boundary — sends through the API flow
additionally through the audit/idempotency/tenant seam.
"""

import os
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.message import Message, MessageChannel
from brokerops_email_sendgrid.adapter import SENDGRID_API_BASE, SendGridEmailAdapter
from brokerops_email_sendgrid.stub import STUB_API_KEY

mcp = FastMCP("email-sendgrid")


def _require_env(name: str) -> str:
    # Same posture as the api wiring: "unset" is the Terraform placeholder for
    # a secret that was never pushed — the same misconfiguration as absent.
    value = os.environ.get(name, "")
    if not value or value == "unset":
        raise RuntimeError(
            f"{name} is required when SENDGRID_BASE_URL points at the real SendGrid API"
        )
    return value


def _adapter() -> SendGridEmailAdapter:
    base_url = os.environ.get("SENDGRID_BASE_URL") or SENDGRID_API_BASE
    if base_url == SENDGRID_API_BASE:
        return SendGridEmailAdapter(
            api_key=_require_env("SENDGRID_API_KEY"),
            from_email=_require_env("SENDGRID_FROM_EMAIL"),
            base_url=base_url,
        )
    # A non-default base URL is a stub/demo endpoint; the stub's key and a
    # synthetic sender are safe defaults there and only there.
    return SendGridEmailAdapter(
        api_key=os.environ.get("SENDGRID_API_KEY", STUB_API_KEY),
        from_email=os.environ.get("SENDGRID_FROM_EMAIL", "demo@example.test"),
        base_url=base_url,
    )


async def send_email(
    recipient: str,
    subject: str,
    body: str,
    contact_id: str = "",
    listing_key: str = "",
) -> str:
    """Send an outbound email to a recipient via SendGrid; returns the provider message id."""
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
