"""Adapter contract tests against the stub — the same shapes the real API returns."""

import json
from datetime import date
from typing import Any

import httpx
import pytest

from brokerops_core.models.contact import ContactCreate
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app


@pytest.fixture
def adapter() -> FUBCRMAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=client)


async def test_search_contacts_maps_fub_people(adapter: FUBCRMAdapter) -> None:
    contacts = await adapter.search_contacts("jordan")
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact.fub_id == "101"
    assert contact.name == "Jordan Pike"
    assert contact.email == "jordan.pike@example.test"
    assert contact.role == "Lead"


async def test_get_contact_found_and_missing(adapter: FUBCRMAdapter) -> None:
    contact = await adapter.get_contact("102")
    assert contact is not None
    assert contact.role == "Hot Prospect"
    assert await adapter.get_contact("999999") is None


async def test_create_contact_roundtrip(adapter: FUBCRMAdapter) -> None:
    created = await adapter.create_contact(
        ContactCreate(first_name="Riley", last_name="Marsh", email="riley.marsh@example.test")
    )
    assert created.name == "Riley Marsh"
    fetched = await adapter.get_contact(created.fub_id)
    assert fetched is not None
    assert fetched.email == "riley.marsh@example.test"


async def test_create_task_with_and_without_contact(adapter: FUBCRMAdapter) -> None:
    standalone = await adapter.create_task("Order yard sign", due_date=date(2026, 6, 15))
    assert standalone.id
    assert standalone.contact_id is None
    attached = await adapter.create_task("Call back", contact_id="101")
    assert attached.contact_id == "101"


async def test_add_note_and_log_call_return_ids(adapter: FUBCRMAdapter) -> None:
    note_id = await adapter.add_note("101", "Showing feedback", "Loved the kitchen.")
    assert note_id
    call_id = await adapter.log_call("101", outcome="interested", note="Wants a second showing")
    assert call_id


async def test_log_call_supplies_fub_required_fields(adapter: FUBCRMAdapter) -> None:
    # FUB demands phone + isIncoming and a fixed outcome vocabulary; the adapter
    # fills phone from the contact, flags the call outbound, and maps the
    # feedback sentiment onto FUB's enum. The stub rejects a non-conforming
    # payload, so a returned id proves the shape is right.
    captured: list[dict[str, Any]] = []

    async def capture(request: httpx.Request) -> None:
        if request.url.path == "/calls":
            captured.append(json.loads(request.content))

    adapter._client.event_hooks["request"].append(capture)
    call_id = await adapter.log_call("101", outcome="positive", note="Loved it")
    assert call_id
    payload = captured[0]
    assert payload["personId"] == 101
    assert payload["isIncoming"] is False
    assert payload["phone"] == "+1-555-0101"
    assert payload["outcome"] == "Interested"


async def test_log_call_omits_unmappable_outcome(adapter: FUBCRMAdapter) -> None:
    # An outcome FUB would reject is dropped (it is optional) rather than sent.
    call_id = await adapter.log_call("101", outcome="vaguely upbeat", note="n/a")
    assert call_id
