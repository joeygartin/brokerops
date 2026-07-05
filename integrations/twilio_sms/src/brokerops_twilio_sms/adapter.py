"""SMSPort adapter speaking the Twilio Messages API (BOP-018).

Plain httpx over Twilio's REST shape (`POST /2010-04-01/Accounts/{sid}/Messages.json`,
form-encoded, HTTP basic auth) — no Twilio SDK; the two calls we make don't earn a
dependency. The same adapter runs against api.twilio.com and the bundled
recorded-shape stub — swapping is a base-URL change. Twilio payload shapes never
leave this module.
"""

import httpx

from brokerops_core.models.message import Message

TWILIO_API_BASE = "https://api.twilio.com"


class TwilioSMSAdapter:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str = "",
        messaging_service_sid: str = "",
        status_callback_url: str = "",
        base_url: str = TWILIO_API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # A2P 10DLC sends usually go out through a Messaging Service (the campaign
        # is attached to it); a bare from-number also works. The deps wiring
        # requires one of the two for the real provider; the stub doesn't care.
        self._account_sid = account_sid
        self._from_number = from_number
        self._messaging_service_sid = messaging_service_sid
        self._status_callback_url = status_callback_url
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            auth=(account_sid, auth_token),
            timeout=15.0,
        )

    async def send(self, message: Message) -> str:
        data: dict[str, str] = {"To": message.recipient, "Body": message.body}
        if self._messaging_service_sid:
            data["MessagingServiceSid"] = self._messaging_service_sid
        else:
            data["From"] = self._from_number
        if self._status_callback_url:
            # Twilio posts delivery-status callbacks (signed) at this URL; the
            # api's /webhooks/twilio-sms transitions the outbound_messages row.
            data["StatusCallback"] = self._status_callback_url
        response = await self._client.post(
            f"/2010-04-01/Accounts/{self._account_sid}/Messages.json", data=data
        )
        response.raise_for_status()
        return str(response.json()["sid"])
