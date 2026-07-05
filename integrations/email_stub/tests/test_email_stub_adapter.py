import httpx
import pytest

from brokerops_core.models.message import Message, MessageChannel
from brokerops_email_stub.adapter import StubEmailAdapter
from brokerops_email_stub.stub import create_stub_app


@pytest.fixture
def adapter() -> StubEmailAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://email.test")
    return StubEmailAdapter(base_url="http://email.test", client=client)


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


async def test_send_returns_a_provider_message_id(adapter: StubEmailAdapter) -> None:
    provider_id = await adapter.send(_message())
    assert provider_id.startswith("stub-email-")


async def test_stub_stores_the_send_for_inspection(adapter: StubEmailAdapter) -> None:
    provider_id = await adapter.send(_message())
    # The stub's GET surface returns exactly what the adapter posted.
    response = await adapter._client.get(f"/messages/{provider_id}")
    assert response.status_code == 200
    stored = response.json()
    assert stored["to"] == "sam@example.com"
    assert stored["subject"] == "Following up on your tour"
    assert stored["channel"] == "email"
    missing = await adapter._client.get("/messages/stub-email-999999")
    assert missing.status_code == 404


async def test_each_send_gets_a_distinct_provider_id(adapter: StubEmailAdapter) -> None:
    first = await adapter.send(_message())
    second = await adapter.send(_message())
    assert first != second


async def test_send_prints_the_email_to_stdout(
    adapter: StubEmailAdapter, capsys: pytest.CaptureFixture[str]
) -> None:
    # Console visibility is the stub's demo contract: compose logs show the send.
    await adapter.send(_message())
    out = capsys.readouterr().out
    assert "to: sam@example.com" in out
    assert "subject: Following up on your tour" in out
