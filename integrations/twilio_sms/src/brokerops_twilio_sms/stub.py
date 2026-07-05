"""Twilio SMS stub — a recorded-shape double of the one Messages-API endpoint we use.

POST /2010-04-01/Accounts/{sid}/Messages.json accepts the same form-encoded body
the real API takes, stores the message, prints it to stdout (so `docker compose
logs` shows the SMS — the email-stub convention), and returns a Message resource
in Twilio's recorded response shape ("queued", SM… sid, error_code null). GET
returns the stored resource for inspection and tests. Nothing ever leaves the
process; demo mode needs zero credentials.
"""

import sys
from itertools import count
from typing import Any

from fastapi import FastAPI, HTTPException, Request


def create_stub_app() -> FastAPI:
    messages: dict[str, dict[str, Any]] = {}
    ids = count(4000)

    app = FastAPI(title="Twilio SMS stub")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/2010-04-01/Accounts/{account_sid}/Messages.json", status_code=201)
    async def create_message(account_sid: str, request: Request) -> dict[str, Any]:
        # Parsed via request.form() (urlencoded — exactly what the adapter and
        # the real API send) rather than Form() params, which would pull in
        # python-multipart for nothing.
        form = await request.form()
        sid = f"SM{next(ids):030x}"
        message = {
            "sid": sid,
            "account_sid": account_sid,
            "api_version": "2010-04-01",
            "to": str(form.get("To", "")),
            "from": str(form.get("From", "")),
            "messaging_service_sid": str(form.get("MessagingServiceSid", "")) or None,
            "body": str(form.get("Body", "")),
            "status": "queued",
            "direction": "outbound-api",
            "num_segments": "1",
            "error_code": None,
            "error_message": None,
            "uri": f"/2010-04-01/Accounts/{account_sid}/Messages/{sid}.json",
        }
        messages[sid] = message
        print(
            f"\n=== SMS (twilio stub, sid {sid}) ===\n"
            f"to: {message['to']}\n"
            f"{message['body']}\n"
            "=== end sms ===\n",
            file=sys.stdout,
            flush=True,
        )
        return message

    @app.get("/2010-04-01/Accounts/{account_sid}/Messages/{message_sid}.json")
    async def get_message(account_sid: str, message_sid: str) -> dict[str, Any]:
        message = messages.get(message_sid)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return message

    return app


app = create_stub_app()
