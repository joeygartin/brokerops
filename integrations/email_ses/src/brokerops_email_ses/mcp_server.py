"""MCP server exposing the SES outbound business-email tool: send_email.

Runs standalone over stdio (`uv run mcp-server-email-ses`), configured by the
same SES_* env vars the api's EMAIL_PROVIDER=ses wiring reads — and with the
same posture: missing config fails loud, never a silent downgrade
(ADR-0014/0015). Note this is the *raw* provider boundary — sends through the
API flow additionally through the audit/idempotency/tenant seam.
"""

import os
from functools import lru_cache
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.message import Message, MessageChannel
from brokerops_email_ses.adapter import DEFAULT_REGION, SESEmailAdapter

mcp = FastMCP("email-ses")


def _require_env(name: str) -> str:
    # "unset" is the Terraform placeholder for a secret that was never pushed —
    # the same misconfiguration as a missing variable.
    value = os.environ.get(name, "")
    if not value or value == "unset":
        raise RuntimeError(f"the SES email MCP server requires {name} to be set to a real value")
    return value


@lru_cache(maxsize=None)
def _cached_adapter(
    access_key_id: str,
    secret_access_key: str,
    from_address: str,
    region: str,
    base_url: str | None,
) -> SESEmailAdapter:
    # Built once per config and reused (BOP-037): a long-lived MCP server must
    # not leak one unclosed httpx.AsyncClient per tool call. Env is fixed for
    # the process lifetime, so this is one adapter in practice. (No real-vs-stub
    # branch to normalize here — SES config is fail-loud on every path.)
    return SESEmailAdapter(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        from_address=from_address,
        region=region,
        base_url=base_url,
    )


def _adapter() -> SESEmailAdapter:
    return _cached_adapter(
        _require_env("SES_ACCESS_KEY_ID"),
        _require_env("SES_SECRET_ACCESS_KEY"),
        _require_env("SES_FROM_ADDRESS"),
        os.environ.get("SES_REGION", DEFAULT_REGION),
        os.environ.get("SES_BASE_URL") or None,
    )


async def send_email(
    recipient: str,
    subject: str,
    body: str,
    contact_id: str = "",
    listing_key: str = "",
) -> str:
    """Send an outbound email via Amazon SES; returns the SES message id."""
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
