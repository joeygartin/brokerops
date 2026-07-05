"""EmailPort adapter speaking the bundled stub provider's REST shape.

The stub is the zero-credential default for the outbound business-email channel
(EMAIL_PROVIDER=stub, ADR-0015): it accepts the message, surfaces it on stdout so
`docker compose logs` shows the send, and returns a provider message id — the same
contract the real provider adapters (SES/SendGrid, BOP-016/017) fulfill. Provider
payload shapes never leave this module.
"""

import httpx

from brokerops_core.models.message import Message

EMAIL_STUB_BASE = "http://localhost:8025"


class StubEmailAdapter:
    def __init__(
        self,
        base_url: str = EMAIL_STUB_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def send(self, message: Message) -> str:
        response = await self._client.post(
            "/messages",
            json={
                "channel": message.channel.value,
                "to": message.recipient,
                "subject": message.subject,
                "body": message.body,
            },
        )
        response.raise_for_status()
        return str(response.json()["id"])
