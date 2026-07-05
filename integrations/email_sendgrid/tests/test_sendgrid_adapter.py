"""Vendor-specific behavior of the SendGrid adapter over the recorded-shape stub.

Port-level meaning (id returned, distinct ids, rejected sends raise) lives in
the shared conformance suite (api/tests/test_email_conformance.py); this file
pins what is SendGrid's alone — the Mail Send payload shape, the header-borne
message id, and the errors-envelope → SendGridApiError mapping.
"""

import httpx
import pytest

from brokerops_core.models.message import Message, MessageChannel
from brokerops_email_sendgrid.adapter import SendGridApiError, SendGridEmailAdapter
from brokerops_email_sendgrid.stub import STUB_API_KEY, create_stub_app

FROM_EMAIL = "updates@brokerage.test"


def _adapter(api_key: str = STUB_API_KEY) -> SendGridEmailAdapter:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_stub_app()),
        base_url="http://sendgrid.test",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return SendGridEmailAdapter(
        api_key=api_key, from_email=FROM_EMAIL, base_url="http://sendgrid.test", client=client
    )


def _message(subject: str = "Following up on your tour") -> Message:
    return Message(
        id="m-1",
        channel=MessageChannel.EMAIL,
        recipient="sam@example.com",
        subject=subject,
        body="Hi Sam, thanks for touring.",
        contact_id="101",
        listing_key="RM1001",
    )


async def test_send_returns_the_header_borne_message_id() -> None:
    adapter = _adapter()
    provider_id = await adapter.send(_message())
    assert provider_id.startswith("stub-sg-")


async def test_send_posts_the_documented_mail_send_shape() -> None:
    app = create_stub_app()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://sendgrid.test",
        headers={"Authorization": f"Bearer {STUB_API_KEY}"},
    )
    adapter = SendGridEmailAdapter(
        api_key=STUB_API_KEY,
        from_email=FROM_EMAIL,
        base_url="http://sendgrid.test",
        client=client,
    )
    provider_id = await adapter.send(_message())
    payload = app.state.outbox[provider_id]
    assert payload["personalizations"] == [{"to": [{"email": "sam@example.com"}]}]
    assert payload["from"] == {"email": FROM_EMAIL}
    assert payload["subject"] == "Following up on your tour"
    assert payload["content"] == [{"type": "text/plain", "value": "Hi Sam, thanks for touring."}]


async def test_wrong_api_key_maps_to_the_vendor_401_reason() -> None:
    adapter = _adapter(api_key="wrong-key")
    with pytest.raises(SendGridApiError) as excinfo:
        await adapter.send(_message())
    assert excinfo.value.status_code == 401
    assert "authorization grant is invalid" in excinfo.value.error_message


async def test_validation_failure_carries_the_vendor_reason() -> None:
    # SendGrid requires a non-empty subject; the errors-envelope message must
    # survive into the exception so audit failure records keep the reason.
    adapter = _adapter()
    with pytest.raises(SendGridApiError) as excinfo:
        await adapter.send(_message(subject=""))
    assert excinfo.value.status_code == 400
    assert "subject" in excinfo.value.error_message


async def test_accepted_response_without_a_message_id_fails_loud() -> None:
    # A 2xx with no X-Message-Id must never persist a SENT row with no
    # provider id — fail loud instead (defensive; the real API always sets it).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sendgrid.test"
    )
    adapter = SendGridEmailAdapter(
        api_key=STUB_API_KEY,
        from_email=FROM_EMAIL,
        base_url="http://sendgrid.test",
        client=client,
    )
    with pytest.raises(SendGridApiError, match="X-Message-Id"):
        await adapter.send(_message())


async def test_undocumented_error_body_falls_back_to_the_http_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sendgrid.test"
    )
    adapter = SendGridEmailAdapter(
        api_key=STUB_API_KEY,
        from_email=FROM_EMAIL,
        base_url="http://sendgrid.test",
        client=client,
    )
    with pytest.raises(SendGridApiError) as excinfo:
        await adapter.send(_message())
    assert excinfo.value.status_code == 503
    assert excinfo.value.error_message == "HTTP 503"


async def test_a_non_email_channel_is_rejected() -> None:
    # The email provider boundary must not silently email an SMS body.
    adapter = _adapter()
    sms = _message().model_copy(update={"channel": MessageChannel.SMS, "subject": ""})
    with pytest.raises(ValueError, match="email only"):
        await adapter.send(sms)
