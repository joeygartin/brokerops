from typing import Protocol


class EmailSender(Protocol):
    """Boundary to transactional email (magic-link delivery in V1).

    The console default logs the message so demo mode stays zero-credential; an
    SMTP adapter sends for real when a provider is configured. core/ depends
    only on this Protocol.
    """

    async def send(self, to: str, subject: str, text: str, html: str | None = None) -> None: ...
