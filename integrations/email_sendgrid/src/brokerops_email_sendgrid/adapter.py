"""EmailPort adapter speaking the SendGrid v3 Mail Send API.

Bearer-key auth (an API key with the Mail Send scope); the same adapter runs
against the bundled recorded-shape stub (offline tests, demo tooling) and
api.sendgrid.com — swapping is a base-URL + key change. SendGrid payload shapes
never leave this module.

Provider quirks the adapter absorbs — none forced a change to `EmailPort` or
`Message`, which is the two-adapter proof BOP-017 exists to produce:

- Success is ``202 Accepted`` with an *empty body*; the provider message id
  rides the ``X-Message-Id`` response header, not JSON. The adapter returns
  that header value as the port's provider id, and fails loud if a 2xx arrives
  without it rather than persist a SENT message with no provider id.
- There is no per-message sender in ``Message``: the from-address is deploy
  config (``SENDGRID_FROM_EMAIL``, an address on a domain the client has
  authenticated in SendGrid — DKIM/SPF, see docs/CLIENT_ONBOARDING.md), the
  same posture as the Vapi adapter's ``phone_number_id``.
- Failures carry SendGrid's documented ``{"errors": [{"message", "field",
  "help"}]}`` envelope; ``SendGridApiError`` surfaces the first message so
  audit failure records keep the vendor's reason (the Sierra precedent).
"""

import httpx

from brokerops_core.models.message import Message, MessageChannel

SENDGRID_API_BASE = "https://api.sendgrid.com"


class SendGridApiError(RuntimeError):
    """A SendGrid API call failed. Carries the vendor's documented failure
    envelope (first ``errors[].message``) so audit failure records keep the
    reason."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"SendGrid API error {status_code}: {message}")
        self.status_code = status_code
        self.error_message = message


def _first_error(response: httpx.Response) -> str:
    """The first ``errors[].message`` of SendGrid's failure envelope, or the
    HTTP status when the body isn't the documented shape."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = errors[0].get("message")
            if message:
                return str(message)
    return f"HTTP {response.status_code}"


class SendGridEmailAdapter:
    def __init__(
        self,
        api_key: str,
        from_email: str,
        base_url: str = SENDGRID_API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._from_email = from_email
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )

    async def send(self, message: Message) -> str:
        # This is the *email* provider boundary: an SMS-channel message reaching
        # it is a wiring bug, and silently emailing an SMS body would hide it.
        if message.channel is not MessageChannel.EMAIL:
            raise ValueError(f"SendGrid sends email only, got channel {message.channel!r}")
        response = await self._client.post(
            "/v3/mail/send",
            json={
                "personalizations": [{"to": [{"email": message.recipient}]}],
                "from": {"email": self._from_email},
                "subject": message.subject,
                "content": [{"type": "text/plain", "value": message.body}],
            },
        )
        if response.status_code >= 300:
            raise SendGridApiError(response.status_code, _first_error(response))
        message_id: str = response.headers.get("X-Message-Id", "")
        if not message_id:
            raise SendGridApiError(
                response.status_code,
                "accepted response is missing the X-Message-Id header",
            )
        return message_id
