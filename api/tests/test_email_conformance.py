"""One EmailPort conformance suite, every provider (BOP-017, ADR-0015).

A port with one adapter is a hypothesis (the ADR-0004 lesson; the CRM suite's
precedent). This suite is the proof that every email adapter honors the same,
provider-neutral contract: it runs each test against the bundled stub provider
AND the SendGrid adapter over its recorded-shape stub — same assertions, no
provider branches. Every assertion here is part of the port's meaning; anything
provider-specific (payload shapes, error envelopes, header-borne ids) belongs
in each integration's own tests.

Notably, SendGrid forced no widening of `EmailPort` or `Message`: the
header-borne message id and the config-level sender identity both fit the
existing contract, so the proof cost nothing at the port.

Adding a provider (SES, BOP-016) is one entry in CASES — build the adapter over
its stub, hand back the two adapters and a normalized sent-message lookup.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import pytest

from brokerops_core.models.message import Message, MessageChannel
from brokerops_core.ports.messaging import EmailPort
from brokerops_email_sendgrid.adapter import SendGridEmailAdapter
from brokerops_email_sendgrid.stub import (
    STUB_API_KEY as SENDGRID_STUB_KEY,
    create_stub_app as create_sendgrid_stub,
)
from brokerops_email_stub.adapter import StubEmailAdapter
from brokerops_email_stub.stub import create_stub_app as create_email_stub


def _typechecks_as_port(adapter: EmailPort) -> EmailPort:
    """mypy proves each adapter satisfies the EmailPort Protocol."""
    return adapter


@dataclass(frozen=True)
class ProviderCase:
    """One provider's adapter over its stub, plus the hooks the suite needs.

    `fetch_sent` normalizes the provider's stored payload to {to, subject,
    body} so the delivery assertion stays provider-neutral. `rejecting_adapter`
    is the same adapter wired so the provider refuses its sends (bad key, wrong
    endpoint — whatever refusal that provider actually has).
    """

    adapter: EmailPort
    rejecting_adapter: EmailPort
    fetch_sent: Callable[[str], Awaitable[dict[str, str]]]


def _stub_case() -> ProviderCase:
    app = create_email_stub()

    def client(base_url: str = "http://email.test") -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)

    adapter = StubEmailAdapter(base_url="http://email.test", client=client())
    # The stub provider has no auth, so its refusal is a wrong endpoint:
    # httpx keeps a base_url path as a prefix, so sends land on a 404 route.
    rejecting = StubEmailAdapter(
        base_url="http://email.test/nowhere", client=client("http://email.test/nowhere")
    )
    inspector = client()

    async def fetch_sent(provider_id: str) -> dict[str, str]:
        response = await inspector.get(f"/messages/{provider_id}")
        response.raise_for_status()
        stored = response.json()
        return {"to": stored["to"], "subject": stored["subject"], "body": stored["body"]}

    return ProviderCase(
        adapter=_typechecks_as_port(adapter),
        rejecting_adapter=_typechecks_as_port(rejecting),
        fetch_sent=fetch_sent,
    )


def _sendgrid_case() -> ProviderCase:
    app = create_sendgrid_stub()

    def adapter(api_key: str) -> SendGridEmailAdapter:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://sendgrid.test",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        return SendGridEmailAdapter(
            api_key=api_key,
            from_email="updates@brokerage.test",
            base_url="http://sendgrid.test",
            client=client,
        )

    async def fetch_sent(provider_id: str) -> dict[str, str]:
        payload = app.state.outbox[provider_id]
        return {
            "to": payload["personalizations"][0]["to"][0]["email"],
            "subject": payload["subject"],
            "body": payload["content"][0]["value"],
        }

    return ProviderCase(
        adapter=_typechecks_as_port(adapter(SENDGRID_STUB_KEY)),
        rejecting_adapter=_typechecks_as_port(adapter("wrong-key")),
        fetch_sent=fetch_sent,
    )


# One entry per provider; BOP-016's SES case lands as one more line here.
CASES: dict[str, Callable[[], ProviderCase]] = {
    "bundled-stub": _stub_case,
    "sendgrid": _sendgrid_case,
}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> ProviderCase:
    factory = CASES[request.param]
    return factory()


def _message(id_: str = "m-1") -> Message:
    return Message(
        id=id_,
        channel=MessageChannel.EMAIL,
        recipient="sam@example.com",
        subject="Following up on your tour",
        body="Hi Sam, thanks for touring.",
        contact_id="101",
        listing_key="RM1001",
    )


async def test_send_returns_a_nonempty_provider_message_id(case: ProviderCase) -> None:
    provider_id = await case.adapter.send(_message())
    assert isinstance(provider_id, str)
    assert provider_id


async def test_distinct_sends_get_distinct_provider_ids(case: ProviderCase) -> None:
    first = await case.adapter.send(_message("m-1"))
    second = await case.adapter.send(_message("m-2"))
    assert first != second


async def test_the_send_reaches_the_provider_intact(case: ProviderCase) -> None:
    provider_id = await case.adapter.send(_message())
    sent = await case.fetch_sent(provider_id)
    assert sent == {
        "to": "sam@example.com",
        "subject": "Following up on your tour",
        "body": "Hi Sam, thanks for touring.",
    }


async def test_a_rejected_send_raises_instead_of_fabricating_an_id(
    case: ProviderCase,
) -> None:
    # The port's failure contract is "raise" (MessageSendService turns it into
    # a FAILED row and re-raises); which exception is the adapter's business.
    with pytest.raises(Exception):  # noqa: B017 — the port names no exception type
        await case.rejecting_adapter.send(_message())
