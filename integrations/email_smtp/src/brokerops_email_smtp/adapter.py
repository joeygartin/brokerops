"""SMTP EmailSender adapter.

Sends via the stdlib `smtplib` (no third-party dependency), wrapped in
`asyncio.to_thread` so the blocking socket work stays off the event loop. Works
with any SMTP provider — SES, Postmark, Mailgun, Gmail, etc. The EmailSender
Protocol is the contract; SMTP specifics stay in this package.
"""

import asyncio
import smtplib
from email.message import EmailMessage


class SMTPEmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        from_addr: str,
        username: str | None = None,
        password: str | None = None,
        use_starttls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._from = from_addr
        self._username = username
        self._password = password
        self._starttls = use_starttls

    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype="html")
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            if self._starttls:
                client.starttls()
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(message)
