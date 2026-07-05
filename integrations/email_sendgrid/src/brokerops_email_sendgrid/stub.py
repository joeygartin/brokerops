"""SendGrid stub — a recorded-shape double of the v3 Mail Send endpoint.

Offline tests and demo tooling run against this with zero credentials. It
mirrors the documented API shapes the adapter touches: bearer-key auth (401
with the documented ``errors`` envelope on a missing/wrong key), the
``personalizations`` / ``from`` / ``subject`` / ``content`` request shape
(field-scoped 400s on drift, so contract tests catch payload drift instead of
production), and the real success shape — ``202 Accepted``, *empty body*,
provider id in the ``X-Message-Id`` header.

Accepted sends land in ``app.state.outbox`` (provider id → request payload)
for test inspection. Real SendGrid has no read-back on Mail Send (message
history is the separately-shaped Email Activity API), so exposing state beats
inventing an endpoint the real API doesn't have.
"""

from itertools import count
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

# The key the stub accepts; anything else gets the real API's 401 envelope.
STUB_API_KEY = "stub-sendgrid-key"


def _errors(status_code: int, message: str, field: str | None = None) -> JSONResponse:
    # Documented failure envelope: an `errors` array of {message, field, help}.
    return JSONResponse(
        {"errors": [{"message": message, "field": field, "help": None}]},
        status_code=status_code,
    )


def create_stub_app() -> FastAPI:
    outbox: dict[str, dict[str, Any]] = {}
    ids = count(9000)

    app = FastAPI(title="SendGrid stub")
    app.state.outbox = outbox

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v3/mail/send")
    async def mail_send(request: Request) -> Response:
        if request.headers.get("Authorization") != f"Bearer {STUB_API_KEY}":
            return _errors(401, "The provided authorization grant is invalid, expired, or revoked")
        body: dict[str, Any] = await request.json()
        personalizations = body.get("personalizations") or []
        recipients = (personalizations[0].get("to") or []) if personalizations else []
        if not recipients or not recipients[0].get("email"):
            return _errors(
                400,
                "The personalizations field is required and must have at least one "
                "personalization with a to recipient.",
                "personalizations",
            )
        if not (body.get("from") or {}).get("email"):
            return _errors(400, "The from object must be provided for every email send.", "from")
        if not body.get("subject"):
            return _errors(400, "The subject is required.", "subject")
        content = body.get("content") or []
        if not content or not content[0].get("value"):
            return _errors(
                400,
                "The content value must be a string at least one character in length.",
                "content",
            )
        message_id = f"stub-sg-{next(ids)}"
        outbox[message_id] = body
        # The real success shape: 202, empty body, id in the header.
        return Response(status_code=202, headers={"X-Message-Id": message_id})

    return app


app = create_stub_app()
