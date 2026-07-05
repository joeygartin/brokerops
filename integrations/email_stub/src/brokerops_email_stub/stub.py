"""Email-provider stub — a zero-credential double of a transactional email API.

POST /messages accepts a send, stores it, prints it to stdout (so the demo's
compose logs show the email — the console-visibility idea of core's
ConsoleEmailSender, applied to the business-comms channel), and returns a
generated provider message id. GET /messages/{id} returns the stored message
for inspection and tests. Nothing ever leaves the process.
"""

import sys
from itertools import count
from typing import Any

from fastapi import FastAPI, HTTPException


def create_stub_app() -> FastAPI:
    messages: dict[str, dict[str, Any]] = {}
    ids = count(9000)

    app = FastAPI(title="Email provider stub")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/messages", status_code=201)
    async def send_message(body: dict[str, Any]) -> dict[str, str]:
        message_id = f"stub-email-{next(ids)}"
        messages[message_id] = body
        print(
            f"\n=== EMAIL (stub provider, id {message_id}) ===\n"
            f"to: {body.get('to', '')}\n"
            f"subject: {body.get('subject', '')}\n"
            f"{body.get('body', '')}\n"
            "=== end email ===\n",
            file=sys.stdout,
            flush=True,
        )
        return {"id": message_id}

    @app.get("/messages/{message_id}")
    async def get_message(message_id: str) -> dict[str, Any]:
        message = messages.get(message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return message

    return app


app = create_stub_app()
