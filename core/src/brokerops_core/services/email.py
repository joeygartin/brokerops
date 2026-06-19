"""Zero-credential email default.

ConsoleEmailSender prints the message instead of sending it, so magic-link login
works in demo/local dev with no email provider — you read the link from the api
logs. It is the email analogue of DemoIdentityVerifier / DeterministicExtractor:
the default lives in core, the credentialed adapter (SMTP) in an integration.

It writes to stdout deliberately rather than via `logging`: its whole purpose is
to surface the link in `docker compose logs`, and an app logger at the default
WARNING level (or uvicorn's untouched root) would swallow an INFO record.
"""

import sys


class ConsoleEmailSender:
    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        print(
            f"\n=== EMAIL (console sender) ===\nto: {to}\nsubject: {subject}\n{text}\n"
            "=== end email ===\n",
            file=sys.stdout,
            flush=True,
        )
