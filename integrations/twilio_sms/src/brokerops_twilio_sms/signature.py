"""Twilio webhook signature validation (X-Twilio-Signature).

Twilio signs every callback: take the full URL it requested, append each POST
parameter name+value sorted alphabetically by name, HMAC-SHA1 the result with the
account's auth token, base64-encode. This lives in the integration package —
Twilio wire specifics never leave `integrations/twilio_sms/` — and the api's
delivery webhook imports it to fail closed on unsigned/invalid callbacks
(the BOP-007 posture).
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping


def compute_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """The signature Twilio would send for this request — for tests and stubs."""
    payload = url + "".join(f"{name}{params[name]}" for name in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def signature_is_valid(
    auth_token: str, url: str, params: Mapping[str, str], signature: str
) -> bool:
    """Constant-time check of a callback's X-Twilio-Signature header."""
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)
