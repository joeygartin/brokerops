import httpx
import pytest

from brokerops_core.models.message import Message, MessageChannel
from brokerops_twilio_sms.adapter import TwilioSMSAdapter
from brokerops_twilio_sms.stub import create_stub_app

ACCOUNT_SID = "ACstub00000000000000000000000000"


def _adapter(
    from_number: str = "+15005550006", messaging_service_sid: str = ""
) -> TwilioSMSAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://sms.test")
    return TwilioSMSAdapter(
        account_sid=ACCOUNT_SID,
        auth_token="stub-token",
        from_number=from_number,
        messaging_service_sid=messaging_service_sid,
        base_url="http://sms.test",
        client=client,
    )


def _message() -> Message:
    return Message(
        id="m-1",
        channel=MessageChannel.SMS,
        recipient="+15551230101",
        body="Hi Sam, thanks for touring 412 Alder Court!",
        contact_id="101",
        listing_key="RM1001",
    )


async def test_send_returns_the_twilio_message_sid() -> None:
    provider_id = await _adapter().send(_message())
    assert provider_id.startswith("SM")


async def test_stub_stores_the_send_in_recorded_shape() -> None:
    adapter = _adapter()
    sid = await adapter.send(_message())
    response = await adapter._client.get(f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/{sid}.json")
    assert response.status_code == 200
    stored = response.json()
    assert stored["to"] == "+15551230101"
    assert stored["from"] == "+15005550006"
    assert stored["body"] == "Hi Sam, thanks for touring 412 Alder Court!"
    assert stored["status"] == "queued"  # the recorded create-response status
    assert stored["error_code"] is None
    missing = await adapter._client.get(f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/SMnope.json")
    assert missing.status_code == 404


async def test_each_send_gets_a_distinct_sid() -> None:
    adapter = _adapter()
    assert await adapter.send(_message()) != await adapter.send(_message())


async def test_messaging_service_sid_replaces_the_from_number() -> None:
    adapter = _adapter(messaging_service_sid="MGstub0000000000000000000000000000")
    sid = await adapter.send(_message())
    response = await adapter._client.get(f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages/{sid}.json")
    stored = response.json()
    assert stored["messaging_service_sid"] == "MGstub0000000000000000000000000000"
    assert stored["from"] == ""


async def test_status_callback_url_is_posted_with_the_send() -> None:
    seen: dict[str, str] = {}

    async def capture(request: httpx.Request) -> httpx.Response:
        seen.update(dict(pair.split("=", 1) for pair in request.content.decode().split("&")))
        return httpx.Response(201, json={"sid": "SMcaptured", "status": "queued"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(capture), base_url="http://sms.test")
    adapter = TwilioSMSAdapter(
        account_sid=ACCOUNT_SID,
        auth_token="stub-token",
        from_number="+15005550006",
        status_callback_url="https://api.example.com/webhooks/twilio-sms",
        base_url="http://sms.test",
        client=client,
    )
    assert await adapter.send(_message()) == "SMcaptured"
    assert seen["StatusCallback"] == "https%3A%2F%2Fapi.example.com%2Fwebhooks%2Ftwilio-sms"


async def test_send_prints_the_sms_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    # Console visibility is the stub's demo contract: compose logs show the send.
    await _adapter().send(_message())
    out = capsys.readouterr().out
    assert "to: +15551230101" in out
    assert "Hi Sam, thanks for touring 412 Alder Court!" in out
