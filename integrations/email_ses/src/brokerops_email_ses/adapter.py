"""EmailPort adapter speaking the Amazon SES v2 API (SendEmail).

The first live provider behind EMAIL_PROVIDER (ADR-0015, BOP-016), reusing the
BOP-008 provisioning groundwork (`scripts/setup_ses.sh` mints the identity and
the send-only IAM credentials). SES payload shapes never leave this module; the
same adapter runs against the bundled recorded-shape stub (tests) and the real
API — swapping is a base-URL + credential change.

Dependency posture — SigV4-over-httpx, not boto3: the repo reaches every
external API through async httpx, and SES usage here is exactly one endpoint
(POST /v2/email/outbound-emails). boto3 would add a large, synchronous
dependency tree (needing thread offloading inside an async port) and bring its
credential-chain inference, which the ADR-0014/0015 explicit-config posture
deliberately rejects. Signature Version 4 is a stable, fully specified HMAC
scheme (`sigv4_signature` below); the stub re-derives the signature from the
raw request with the same primitives, so canonicalization is contract-tested
offline.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from brokerops_core.models.message import Message, MessageChannel

DEFAULT_REGION = "us-west-2"
SEND_EMAIL_PATH = "/v2/email/outbound-emails"
SIGV4_ALGORITHM = "AWS4-HMAC-SHA256"
_CONTENT_TYPE = "application/json"


def ses_api_base(region: str) -> str:
    return f"https://email.{region}.amazonaws.com"


class SESApiError(RuntimeError):
    """An SES API call failed. Carries the provider's error type and message so
    the audit ledger's failure records (RecordingEmail `error` column) and the
    FAILED row in `outbound_messages` keep the provider's reason."""

    def __init__(self, status_code: int, error_type: str, message: str) -> None:
        super().__init__(f"SES API error {status_code} ({error_type}): {message}")
        self.status_code = status_code
        self.error_type = error_type
        self.error_message = message


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sigv4_signature(
    *,
    secret_access_key: str,
    method: str,
    path: str,
    headers: Mapping[str, str],
    signed_headers: Sequence[str],
    body: bytes,
    amz_date: str,
    region: str,
) -> str:
    """The AWS Signature Version 4 hex signature for one request (service `ses`).

    Shared by the adapter (signing outbound requests) and the stub (re-deriving
    the signature from the received wire request to verify it) — one
    canonicalization, checked from both sides.

    Guard-rails (BOP-037): this implementation is correct ONLY for the fixed
    request shape this adapter sends. It does NOT handle query-string
    canonicalization (the canonical query string is hardcoded empty — a path
    with a query would sign wrong), multi-value headers, or headers whose
    values need internal-whitespace folding (each signed header is assumed
    single-valued and is only edge-trimmed). If you touch this function — or
    start sending a different request shape through it — re-verify with a
    manual live send against real SES, not just the stub.
    """
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in signed_headers)
    canonical_request = "\n".join(
        [
            method,
            path,
            "",  # canonical query string (the send endpoint takes none)
            canonical_headers,
            ";".join(signed_headers),
            hashlib.sha256(body).hexdigest(),
        ]
    )
    date = amz_date[:8]
    scope = f"{date}/{region}/ses/aws4_request"
    string_to_sign = "\n".join(
        [
            SIGV4_ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    key = _hmac_sha256(f"AWS4{secret_access_key}".encode(), date)
    for part in (region, "ses", "aws4_request"):
        key = _hmac_sha256(key, part)
    return hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()


class SESEmailAdapter:
    def __init__(
        self,
        *,
        access_key_id: str,
        secret_access_key: str,
        from_address: str,
        region: str = DEFAULT_REGION,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._from_address = from_address
        self._region = region
        base = (base_url or ses_api_base(region)).rstrip("/")
        # Sign the exact Host header httpx will send (host[:port] from the URL),
        # so the signed value always matches the wire value.
        self._host = httpx.URL(base).netloc.decode()
        self._client = client or httpx.AsyncClient(base_url=base, timeout=15.0)

    async def send(self, message: Message) -> str:
        if message.channel is not MessageChannel.EMAIL:
            raise ValueError(f"SES sends email only, not {message.channel.value!r} messages")
        payload = {
            "FromEmailAddress": self._from_address,
            "Destination": {"ToAddresses": [message.recipient]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": message.body, "Charset": "UTF-8"}},
                }
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        response = await self._client.post(
            SEND_EMAIL_PATH, content=body, headers=self._signed_headers(body)
        )
        if response.status_code >= 300:
            raise SESApiError(response.status_code, _error_type(response), _error_message(response))
        return str(response.json()["MessageId"])

    def _signed_headers(self, body: bytes) -> dict[str, str]:
        amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        headers = {"content-type": _CONTENT_TYPE, "host": self._host, "x-amz-date": amz_date}
        signed = sorted(headers)
        signature = sigv4_signature(
            secret_access_key=self._secret_access_key,
            method="POST",
            path=SEND_EMAIL_PATH,
            headers=headers,
            signed_headers=signed,
            body=body,
            amz_date=amz_date,
            region=self._region,
        )
        scope = f"{amz_date[:8]}/{self._region}/ses/aws4_request"
        headers["authorization"] = (
            f"{SIGV4_ALGORITHM} Credential={self._access_key_id}/{scope}, "
            f"SignedHeaders={';'.join(signed)}, Signature={signature}"
        )
        return headers


def _error_type(response: httpx.Response) -> str:
    # e.g. "MessageRejected", sometimes suffixed with a doc URI after ":".
    raw = response.headers.get("x-amzn-ErrorType", "")
    error_type = raw.split(":")[0].split("#")[-1]
    return error_type or "UnknownError"


def _error_message(response: httpx.Response) -> str:
    try:
        detail: Any = response.json()
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        # AWS error envelopes are inconsistent about the key's case: SES v2
        # documents lowercase "message", but several AWS services (and some
        # gateway-level errors) emit "Message" — accept both (BOP-037).
        for key in ("message", "Message"):
            if detail.get(key):
                return str(detail[key])
    return f"HTTP {response.status_code}"
