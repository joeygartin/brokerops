"""Adapter contract tests against the stub — the same shapes the documented API returns."""

import json
from datetime import date
from typing import Any

import httpx
import pytest

from brokerops_core.models.contact import ContactCreate
from brokerops_sierra_crm.adapter import SierraCRMAdapter
from brokerops_sierra_crm.stub import (
    STUB_TASK_ANCHOR_LEAD_ID,
    STUB_TASK_ASSIGNEE_ID,
    create_stub_app,
)


def _adapter() -> SierraCRMAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://sierra.test",
        headers={"Sierra-ApiKey": "stub-key"},
    )
    return SierraCRMAdapter(
        api_key="stub-key",
        base_url="http://sierra.test",
        client=client,
        task_assignee_id=STUB_TASK_ASSIGNEE_ID,
        task_anchor_lead_id=STUB_TASK_ANCHOR_LEAD_ID,
    )


@pytest.fixture
def adapter() -> SierraCRMAdapter:
    return _adapter()


async def test_search_contacts_maps_sierra_leads(adapter: SierraCRMAdapter) -> None:
    contacts = await adapter.search_contacts("morgan")
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact.crm_id == "501"
    assert contact.name == "Morgan Ellis"
    assert contact.email == "morgan.ellis@example.test"
    assert contact.role == "New"


async def test_search_by_email_uses_the_email_field(adapter: SierraCRMAdapter) -> None:
    contacts = await adapter.search_contacts("priya.nair@example.test")
    assert [c.crm_id for c in contacts] == ["502"]


async def test_get_contact_found_missing_and_by_email(adapter: SierraCRMAdapter) -> None:
    contact = await adapter.get_contact("502")
    assert contact is not None
    assert contact.role == "Active"
    assert await adapter.get_contact("999999") is None
    # Sierra addresses leads by id or email; both resolve through the port.
    by_email = await adapter.get_contact("sam.okafor@example.test")
    assert by_email is not None and by_email.crm_id == "503"


async def test_create_contact_registers_then_reads_back(adapter: SierraCRMAdapter) -> None:
    created = await adapter.create_contact(
        ContactCreate(first_name="Riley", last_name="Marsh", email="riley.marsh@example.test")
    )
    assert created.name == "Riley Marsh"
    fetched = await adapter.get_contact(created.crm_id)
    assert fetched is not None
    assert fetched.email == "riley.marsh@example.test"


async def test_create_contact_without_email_is_rejected(adapter: SierraCRMAdapter) -> None:
    # Sierra requires an email to register a lead; the adapter fails loud
    # before any HTTP call rather than let the vendor 400 surface raw.
    with pytest.raises(ValueError, match="email"):
        await adapter.create_contact(ContactCreate(first_name="No", last_name="Email"))


async def test_create_contact_generates_password_and_suppresses_email(
    adapter: SierraCRMAdapter,
) -> None:
    captured: list[dict[str, Any]] = []

    async def capture(request: httpx.Request) -> None:
        if request.url.path == "/leads":
            captured.append(json.loads(request.content))

    adapter._client.event_hooks["request"].append(capture)
    await adapter.create_contact(
        ContactCreate(first_name="Riley", last_name="Marsh", email="riley.marsh@example.test")
    )
    payload = captured[0]
    assert payload["password"]  # required by Sierra, generated per create
    assert payload["sendRegistrationEmail"] is False


async def test_create_task_attached_to_contact(adapter: SierraCRMAdapter) -> None:
    task = await adapter.create_task("Call back", due_date=date(2026, 7, 10), contact_id="502")
    assert task.id
    assert task.contact_id == "502"
    assert task.due_date == date(2026, 7, 10)


async def test_create_task_without_contact_uses_anchor_but_echoes_none(
    adapter: SierraCRMAdapter,
) -> None:
    captured: list[str] = []

    async def capture(request: httpx.Request) -> None:
        if request.url.path.endswith("/task") and request.method == "POST":
            captured.append(request.url.path)

    adapter._client.event_hooks["request"].append(capture)
    task = await adapter.create_task("Order yard sign", due_date=date(2026, 7, 10))
    assert task.contact_id is None  # anchor is plumbing, not semantics
    assert captured == [f"/leads/{STUB_TASK_ANCHOR_LEAD_ID}/task"]


async def test_create_task_supplies_sierra_required_fields(adapter: SierraCRMAdapter) -> None:
    # Sierra demands toUserId + dueOn and a known taskType; the stub rejects a
    # non-conforming payload, so a returned id proves the shape is right.
    captured: list[dict[str, Any]] = []

    async def capture(request: httpx.Request) -> None:
        if request.url.path.endswith("/task") and request.method == "POST":
            captured.append(json.loads(request.content))

    adapter._client.event_hooks["request"].append(capture)
    task = await adapter.create_task("Order yard sign", due_date=date(2026, 7, 10))
    assert task.id
    payload = captured[0]
    assert payload["toUserId"] == STUB_TASK_ASSIGNEE_ID
    assert payload["dueOn"] == "2026-07-10T00:00:00Z"
    assert payload["taskType"] == "Other"
    assert payload["contents"] == "Order yard sign"


async def test_add_note_folds_subject_into_message(adapter: SierraCRMAdapter) -> None:
    captured: list[dict[str, Any]] = []

    async def capture(request: httpx.Request) -> None:
        if request.url.path.endswith("/note"):
            captured.append(json.loads(request.content))

    adapter._client.event_hooks["request"].append(capture)
    note_id = await adapter.add_note("501", "Showing feedback", "Loved the kitchen.")
    assert note_id
    assert captured[0]["message"] == "Showing feedback\n\nLoved the kitchen."


async def test_log_call_journals_as_note(adapter: SierraCRMAdapter) -> None:
    # Sierra's public API has no call-log write endpoint; the adapter journals
    # the call as a note carrying outcome + duration (ADR-0015).
    captured: list[dict[str, Any]] = []

    async def capture(request: httpx.Request) -> None:
        if request.url.path.endswith("/note"):
            captured.append(json.loads(request.content))

    adapter._client.event_hooks["request"].append(capture)
    call_id = await adapter.log_call(
        "501", outcome="positive", note="Wants a second showing", duration_seconds=95
    )
    assert call_id
    message = captured[0]["message"]
    assert "outcome: positive" in message
    assert "duration: 95s" in message
    assert "Wants a second showing" in message


async def test_unknown_lead_writes_surface_as_http_errors(adapter: SierraCRMAdapter) -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.add_note("999999", "s", "b")
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.create_task("Task", due_date=date(2026, 7, 10), contact_id="999999")
