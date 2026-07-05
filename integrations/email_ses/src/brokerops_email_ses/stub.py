"""Amazon SES v2 stub — a recorded-shape double of the SendEmail endpoint.

POST /v2/email/outbound-emails answers with the shapes the real API returns:
200 ``{"MessageId": ...}`` on success, 403 ``{"message": ...}`` (with an
``x-amzn-ErrorType`` header) for authentication failures, and the sandbox's
400 ``MessageRejected`` for an unverified sending identity. Unlike a plain
canned double, the stub *verifies* the request's SigV4 signature — it
re-derives the signature from the raw wire request (method, path, signed
headers, body) with the shared ``sigv4_signature`` primitives — so the
adapter's canonicalization is contract-tested offline, the way SES itself
would check it. Nothing ever leaves the process.

``GET /_stub/messages/{id}`` is an inspection surface for tests (the real API
has no read-back endpoint; the ``_stub`` prefix marks it as outside the
recorded shape).
"""

import hmac
import json
import re
from itertools import count
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from brokerops_email_ses.adapter import SIGV4_ALGORITHM, sigv4_signature

# Deliberately fake, low-entropy stand-ins (never real-key-shaped): the stub
# only ever checks that the adapter signed with the same pair.
STUB_ACCESS_KEY_ID = "fake-ses-access-key-id"
STUB_SECRET_ACCESS_KEY = "fake-ses-secret-key"
# Verified identities, SES-style: an entry is a full address or a bare domain.
STUB_VERIFIED_IDENTITIES = frozenset({"demo.example.com"})
STUB_REGION_LABEL = "US-WEST-2"

_CREDENTIAL_RE = re.compile(
    r"^Credential=(?P<key_id>[^/]+)/(?P<date>\d{8})/(?P<region>[^/]+)/ses/aws4_request$"
)


def _error(status_code: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"message": message},
        status_code=status_code,
        headers={"x-amzn-ErrorType": error_type},
    )


def _verify_sigv4(
    request: Request, body: bytes, access_key_id: str, secret_access_key: str
) -> JSONResponse | None:
    """Re-derive the request's signature from the wire; None means authenticated."""
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith(f"{SIGV4_ALGORITHM} "):
        return _error(403, "MissingAuthenticationTokenException", "Missing Authentication Token")
    parts = dict(
        part.split("=", 1)
        for part in authorization.removeprefix(f"{SIGV4_ALGORITHM} ").split(", ")
        if "=" in part
    )
    credential = _CREDENTIAL_RE.match(f"Credential={parts.get('Credential', '')}")
    signed_headers = [name for name in parts.get("SignedHeaders", "").split(";") if name]
    claimed_signature = parts.get("Signature", "")
    amz_date = request.headers.get("x-amz-date", "")
    if credential is None or not signed_headers or not claimed_signature or not amz_date:
        return _error(
            403,
            "IncompleteSignatureException",
            "Authorization header requires existence of "
            "'Credential', 'SignedHeaders', and 'Signature' parameters and an X-Amz-Date header.",
        )
    if credential.group("key_id") != access_key_id:
        return _error(
            403,
            "UnrecognizedClientException",
            "The security token included in the request is invalid.",
        )
    expected = sigv4_signature(
        secret_access_key=secret_access_key,
        method=request.method,
        path=request.url.path,
        headers={name: request.headers.get(name, "") for name in signed_headers},
        signed_headers=signed_headers,
        body=body,
        amz_date=amz_date,
        region=credential.group("region"),
    )
    if not hmac.compare_digest(expected, claimed_signature):
        return _error(
            403,
            "InvalidSignatureException",
            "The request signature we calculated does not match the signature you provided. "
            "Check your AWS Secret Access Key and signing method.",
        )
    return None


def create_stub_app(
    *,
    access_key_id: str = STUB_ACCESS_KEY_ID,
    secret_access_key: str = STUB_SECRET_ACCESS_KEY,
    verified_identities: frozenset[str] = STUB_VERIFIED_IDENTITIES,
) -> FastAPI:
    sent: dict[str, dict[str, Any]] = {}
    ids = count(1)

    app = FastAPI(title="Amazon SES v2 stub")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v2/email/outbound-emails")
    async def send_email(request: Request) -> JSONResponse:
        body = await request.body()
        denied = _verify_sigv4(request, body, access_key_id, secret_access_key)
        if denied is not None:
            return denied
        try:
            payload = json.loads(body)
        except ValueError:
            return _error(400, "BadRequestException", "Malformed request body")
        sender = str(payload.get("FromEmailAddress") or "")
        recipients = (payload.get("Destination") or {}).get("ToAddresses") or []
        if not sender or not recipients:
            return _error(
                400, "BadRequestException", "FromEmailAddress and Destination are required"
            )
        # The sandbox / unverified-identity rejection, recorded shape: an
        # identity is verified as a full address or as its bare domain.
        domain = sender.rsplit("@", 1)[-1].lower()
        if sender.lower() not in verified_identities and domain not in verified_identities:
            return _error(
                400,
                "MessageRejected",
                "Email address is not verified. The following identities failed "
                f"the check in region {STUB_REGION_LABEL}: {sender}",
            )
        message_id = f"0100019{next(ids):09x}-ses-stub"
        sent[message_id] = dict(payload)
        return JSONResponse({"MessageId": message_id}, status_code=200)

    @app.get("/_stub/messages/{message_id}")
    async def get_message(message_id: str) -> dict[str, Any]:
        message = sent.get(message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return message

    return app


app = create_stub_app()
