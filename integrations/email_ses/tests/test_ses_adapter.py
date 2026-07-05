"""SESEmailAdapter against the recorded-shape stub (BOP-016, ADR-0015).

All offline: the stub verifies the SigV4 signature from the raw wire request,
so a green send here proves the adapter's signing/canonicalization, and the
failure cases prove the provider's error detail survives into SESApiError
(what RecordingEmail persists to the ledger's `error` column). The lifecycle
tests prove the FAILED/SENT mapping onto the Message status model through the
same MessageSendService seam the stub provider uses — no per-adapter code.
"""

import httpx
import pytest

from brokerops_core.models.message import Message, MessageChannel, MessageStatus
from brokerops_core.ports.messaging import EmailPort
from brokerops_core.services.message_send import MessageSendService
from brokerops_email_ses.adapter import SESApiError, SESEmailAdapter
from brokerops_email_ses.stub import (
    STUB_ACCESS_KEY_ID,
    STUB_SECRET_ACCESS_KEY,
    create_stub_app,
)

FROM_ADDRESS = "updates@demo.example.com"


def _adapter(
    secret_access_key: str = STUB_SECRET_ACCESS_KEY,
    access_key_id: str = STUB_ACCESS_KEY_ID,
    from_address: str = FROM_ADDRESS,
) -> SESEmailAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://ses.test")
    return SESEmailAdapter(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        from_address=from_address,
        base_url="http://ses.test",
        client=client,
    )


def _message() -> Message:
    return Message(
        id="m-1",
        channel=MessageChannel.EMAIL,
        recipient="sam@example.com",
        subject="Following up on your tour",
        body="Hi Sam, thanks for touring.",
        contact_id="101",
        listing_key="RM1001",
    )


async def test_send_returns_the_ses_message_id() -> None:
    provider_id = await _adapter().send(_message())
    assert provider_id.endswith("-ses-stub")


async def test_each_send_gets_a_distinct_provider_id() -> None:
    adapter = _adapter()
    assert await adapter.send(_message()) != await adapter.send(_message())


async def test_send_posts_the_recorded_sendemail_shape() -> None:
    adapter = _adapter()
    provider_id = await adapter.send(_message())
    response = await adapter._client.get(f"/_stub/messages/{provider_id}")
    assert response.status_code == 200
    stored = response.json()
    assert stored["FromEmailAddress"] == FROM_ADDRESS
    assert stored["Destination"]["ToAddresses"] == ["sam@example.com"]
    assert stored["Content"]["Simple"]["Subject"]["Data"] == "Following up on your tour"
    assert stored["Content"]["Simple"]["Body"]["Text"]["Data"] == "Hi Sam, thanks for touring."
    missing = await adapter._client.get("/_stub/messages/0100019000000000-ses-stub")
    assert missing.status_code == 404


async def test_a_wrong_secret_is_rejected_by_signature_verification() -> None:
    with pytest.raises(SESApiError) as excinfo:
        await _adapter(secret_access_key="fake-wrong-secret").send(_message())
    assert excinfo.value.status_code == 403
    assert excinfo.value.error_type == "InvalidSignatureException"
    # str(exc) is what RecordingEmail persists — the provider reason must survive.
    assert "signature" in str(excinfo.value).lower()


async def test_an_unknown_access_key_id_is_rejected() -> None:
    with pytest.raises(SESApiError) as excinfo:
        await _adapter(access_key_id="fake-other-key-id").send(_message())
    assert excinfo.value.status_code == 403
    assert excinfo.value.error_type == "UnrecognizedClientException"


async def test_an_unverified_sender_maps_the_provider_rejection() -> None:
    with pytest.raises(SESApiError) as excinfo:
        await _adapter(from_address="updates@unverified.example.org").send(_message())
    assert excinfo.value.status_code == 400
    assert excinfo.value.error_type == "MessageRejected"
    assert "not verified" in excinfo.value.error_message


async def test_non_email_channels_are_refused() -> None:
    sms = _message().model_copy(update={"channel": MessageChannel.SMS})
    with pytest.raises(ValueError, match="email only"):
        await _adapter().send(sms)


def test_ses_adapter_satisfies_the_email_port_protocol() -> None:
    # Checked by mypy strict: assigning the adapter to the Protocol type is the
    # contract assertion (EmailPort is not runtime_checkable by design).
    port: EmailPort = _adapter()
    assert callable(port.send)


# ── The same seam as the stub: drafted → sent/failed on the Message model ──


class DictMessageStore:
    def __init__(self) -> None:
        self.rows: dict[str, Message] = {}

    async def save_message(self, message: Message) -> None:
        self.rows[message.id] = message

    async def get_message(self, message_id: str) -> Message | None:
        return self.rows.get(message_id)

    async def list_messages(self, contact_id: str | None = None, limit: int = 100) -> list[Message]:
        return list(self.rows.values())[:limit]


PARAMS = {
    "recipient_name": "Sam",
    "listing_address": "412 Alder Court",
    "sender_name": "The Rivermouth Team",
}


async def test_lifecycle_success_records_the_ses_message_id() -> None:
    store = DictMessageStore()
    service = MessageSendService(email=_adapter(), store=store)
    message = await service.send_email(
        recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
    )
    assert message.status is MessageStatus.SENT
    assert message.provider_message_id.endswith("-ses-stub")


async def test_lifecycle_failure_persists_failed_and_reraises_the_provider_error() -> None:
    store = DictMessageStore()
    rejected = _adapter(from_address="updates@unverified.example.org")
    service = MessageSendService(email=rejected, store=store)
    with pytest.raises(SESApiError, match="MessageRejected"):
        await service.send_email(
            recipient="sam@example.com", template_ref="showing_followup:v1", params=PARAMS
        )
    (row,) = store.rows.values()
    assert row.status is MessageStatus.FAILED
    assert row.provider_message_id == ""
