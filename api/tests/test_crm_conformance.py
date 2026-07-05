"""One CRMPort conformance suite, two vendors (BOP-022 / ADR-0015).

A port with one adapter is a hypothesis (the ADR-0004 lesson); this suite is
the proof that both CRM adapters honor the same, vendor-neutral contract. It
runs each test against the FollowUpBoss adapter over the FUB stub AND the
Sierra adapter over the Sierra stub — same assertions, no vendor branches.
Every assertion here is part of the port's meaning; vendor-specific behavior
belongs in each integration's own tests.
"""

from dataclasses import dataclass
from datetime import date

import httpx
import pytest

from brokerops_core.models.contact import ContactCreate
from brokerops_core.ports.crm import CRMPort
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app as create_fub_stub
from brokerops_sierra_crm.adapter import SierraCRMAdapter
from brokerops_sierra_crm.stub import (
    STUB_TASK_ANCHOR_LEAD_ID,
    STUB_TASK_ASSIGNEE_ID,
    create_stub_app as create_sierra_stub,
)


def _typechecks_as_port(adapter: CRMPort) -> CRMPort:
    """mypy proves each adapter satisfies the (widened) CRMPort Protocol."""
    return adapter


@dataclass(frozen=True)
class VendorCase:
    """One vendor's adapter over its stub, plus that stub's seeded facts."""

    adapter: CRMPort
    seeded_contact_id: str
    seeded_name_query: str
    seeded_email: str


def _fub_case() -> VendorCase:
    transport = httpx.ASGITransport(app=create_fub_stub())
    client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    adapter = FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=client)
    return VendorCase(
        adapter=_typechecks_as_port(adapter),
        seeded_contact_id="101",
        seeded_name_query="jordan",
        seeded_email="jordan.pike@example.test",
    )


def _sierra_case() -> VendorCase:
    transport = httpx.ASGITransport(app=create_sierra_stub())
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://sierra.test",
        headers={"Sierra-ApiKey": "stub-key"},
    )
    adapter = SierraCRMAdapter(
        api_key="stub-key",
        base_url="http://sierra.test",
        client=client,
        task_assignee_id=STUB_TASK_ASSIGNEE_ID,
        task_anchor_lead_id=STUB_TASK_ANCHOR_LEAD_ID,
    )
    return VendorCase(
        adapter=_typechecks_as_port(adapter),
        seeded_contact_id="501",
        seeded_name_query="morgan",
        seeded_email="morgan.ellis@example.test",
    )


@pytest.fixture(params=["followupboss", "sierra"])
def case(request: pytest.FixtureRequest) -> VendorCase:
    return _fub_case() if request.param == "followupboss" else _sierra_case()


async def test_search_by_name_returns_core_contacts(case: VendorCase) -> None:
    contacts = await case.adapter.search_contacts(case.seeded_name_query)
    assert [c.crm_id for c in contacts] == [case.seeded_contact_id]
    contact = contacts[0]
    assert contact.name and contact.role  # vendor-neutral read-through shape
    assert contact.email == case.seeded_email


async def test_search_by_email_finds_the_seeded_contact(case: VendorCase) -> None:
    contacts = await case.adapter.search_contacts(case.seeded_email)
    assert case.seeded_contact_id in [c.crm_id for c in contacts]


async def test_get_contact_roundtrip_and_missing(case: VendorCase) -> None:
    contact = await case.adapter.get_contact(case.seeded_contact_id)
    assert contact is not None
    assert contact.crm_id == case.seeded_contact_id
    assert await case.adapter.get_contact("999999") is None


async def test_create_contact_roundtrips_through_the_vendor(case: VendorCase) -> None:
    created = await case.adapter.create_contact(
        ContactCreate(first_name="Riley", last_name="Marsh", email="riley.marsh@example.test")
    )
    assert created.crm_id
    assert created.name == "Riley Marsh"
    fetched = await case.adapter.get_contact(created.crm_id)
    assert fetched is not None
    assert fetched.email == "riley.marsh@example.test"


async def test_create_task_attached_to_a_contact(case: VendorCase) -> None:
    task = await case.adapter.create_task(
        "Call back", due_date=date(2026, 7, 10), contact_id=case.seeded_contact_id
    )
    assert task.id
    assert task.name == "Call back"
    assert task.due_date == date(2026, 7, 10)
    assert task.contact_id == case.seeded_contact_id


async def test_create_task_without_a_contact(case: VendorCase) -> None:
    # contact_id=None means "not tied to a contact" — however a vendor anchors
    # it internally, the port-level result must echo None back (ADR-0015).
    task = await case.adapter.create_task("Order yard sign", due_date=date(2026, 7, 10))
    assert task.id
    assert task.contact_id is None


async def test_add_note_returns_an_id(case: VendorCase) -> None:
    note_id = await case.adapter.add_note(
        case.seeded_contact_id, "Showing feedback", "Loved the kitchen."
    )
    assert note_id


async def test_log_call_accepts_sentiment_outcomes(case: VendorCase) -> None:
    # The feedback workflow hands the port a sentiment; every vendor must
    # accept it (mapping or journaling is the adapter's business).
    for outcome in ("positive", "neutral", "negative"):
        record_id = await case.adapter.log_call(
            case.seeded_contact_id, outcome=outcome, note="n/a", duration_seconds=60
        )
        assert record_id
