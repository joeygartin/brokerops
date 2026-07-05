"""MCP server exposing the outbound SMS tool: send_sms.

Runs standalone over stdio (`uv run mcp-server-twilio-sms`); points at any
Twilio-shaped endpoint via SMS_BASE_URL (real API by default, stub in demo mode).
Against the real API host, the account SID, auth token, and a sender
(from-number or Messaging Service) must all be explicit — fail-loud, mirroring
the api wiring (the sierra MCP precedent): stub placeholders must never leak
into a request against api.twilio.com. Pointing SMS_BASE_URL at a stub keeps
permissive stub defaults. Note this is the *raw* provider boundary — sends
through the API flow additionally through the audit/idempotency/tenant seam.
"""

import os
from functools import lru_cache
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.message import Message, MessageChannel
from brokerops_twilio_sms.adapter import TWILIO_API_BASE, TwilioSMSAdapter

mcp = FastMCP("twilio-sms")


def _require_env(name: str) -> str:
    # Same posture as the api wiring: "unset" is the Terraform placeholder for
    # a secret that was never pushed — the same misconfiguration as absent.
    value = os.environ.get(name, "")
    if not value or value == "unset":
        raise RuntimeError(f"{name} is required when SMS_BASE_URL points at the real Twilio API")
    return value


def _is_real_api(base_url: str) -> bool:
    # Normalize before the real-vs-stub compare (BOP-037): a trailing-slash or
    # case variant of the real host must take the real-API branch — never the
    # permissive stub one with placeholder credentials.
    return base_url.strip().rstrip("/").lower() == TWILIO_API_BASE


@lru_cache(maxsize=None)
def _cached_adapter(
    account_sid: str,
    auth_token: str,
    from_number: str,
    messaging_service_sid: str,
    base_url: str,
) -> TwilioSMSAdapter:
    # Built once per config and reused (BOP-037): a long-lived MCP server must
    # not leak one unclosed httpx.AsyncClient per tool call. Env is fixed for
    # the process lifetime, so this is one adapter in practice.
    return TwilioSMSAdapter(
        account_sid=account_sid,
        auth_token=auth_token,
        from_number=from_number,
        messaging_service_sid=messaging_service_sid,
        base_url=base_url,
    )


def _adapter() -> TwilioSMSAdapter:
    base_url = os.environ.get("SMS_BASE_URL", TWILIO_API_BASE)
    if _is_real_api(base_url):
        account_sid = _require_env("TWILIO_ACCOUNT_SID")
        auth_token = _require_env("TWILIO_AUTH_TOKEN")
        from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
        messaging_service_sid = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")
        if (not from_number or from_number == "unset") and (
            not messaging_service_sid or messaging_service_sid == "unset"
        ):
            raise RuntimeError(
                "TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID is required when "
                "SMS_BASE_URL points at the real Twilio API"
            )
        return _cached_adapter(
            account_sid, auth_token, from_number, messaging_service_sid, TWILIO_API_BASE
        )
    # A non-default base URL is a stub/demo endpoint; permissive defaults are
    # safe there and only there.
    return _cached_adapter(
        os.environ.get("TWILIO_ACCOUNT_SID", "ACstub00000000000000000000000000"),
        os.environ.get("TWILIO_AUTH_TOKEN", "stub-token"),
        os.environ.get("TWILIO_FROM_NUMBER", "+15005550006"),
        os.environ.get("TWILIO_MESSAGING_SERVICE_SID", ""),
        base_url,
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
