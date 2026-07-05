"""Unit coverage for output/egress filtering (BOP-012).

Proves the OUTPUT half of the uniform tool seam: a response carrying a foreign tenant
identifier is BLOCKED whole (fail closed — the caller receives the failure, never a
partial payload, and a security event lands on the existing ledger), while secret-shaped
values and role-restricted PII are REDACTED in place with a marker. Rules are data —
``tenant_values`` for the tenant surface, the audit trail's secret-key hints plus the
``SECRET_VALUE_PATTERNS`` table for secrets, and ``Pii`` field annotations for PII —
never per-tool code.
"""

import logging
from datetime import date
from typing import Annotated, Any

import pytest
from pydantic import BaseModel

from brokerops_core.models.contact import Contact
from brokerops_core.models.message import Message
from brokerops_core.models.mutation import MutationRecord
from brokerops_core.models.sensitivity import CONTACT_PII, Pii
from brokerops_core.models.transaction import Transaction, TransactionStage
from brokerops_core.ports.identity import Role
from brokerops_core.services.egress import (
    EGRESS_FILTERED_MARKER,
    PII_REDACTED,
    SECRET_REDACTED,
    EgressBlockedError,
    filter_port_egress,
    filter_tool_response,
    guard_tool_ports,
    scrub_payload,
)
from brokerops_core.services.tenancy import TenantContextMissing, tenant_scope
from brokerops_core.services.tool_authz import AUTHORIZED_MARKER


class _CollectingAudit:
    def __init__(self) -> None:
        self.records: list[MutationRecord] = []

    async def record(self, record: MutationRecord) -> None:
        self.records.append(record)

    async def list(
        self, workflow_run_id: str | None = None, limit: int = 200
    ) -> list[MutationRecord]:
        return list(self.records)


def _txn(tenant_id: str) -> Transaction:
    return Transaction(
        tenant_id=tenant_id,
        id="TXN-1",
        listing_key="L1",
        stage=TransactionStage.UNDER_CONTRACT,
        contract_date=date(2026, 1, 1),
    )


def _contact() -> Contact:
    return Contact(crm_id="42", name="Jane Buyer", email="jane@example.com", phone="+15305550100")


# ---- rule 1: cross-tenant identifier ⇒ BLOCK (fail closed) ------------------------------


async def test_foreign_tenant_in_a_response_blocks_the_whole_response() -> None:
    audit = _CollectingAudit()
    with tenant_scope("demo"):
        with pytest.raises(EgressBlockedError) as exc:
            await filter_tool_response(
                "get_transaction",
                _txn("other-brokerage"),
                recipient_role=Role.OPERATOR,
                audit=audit,
            )
    assert exc.value.attempted == "other-brokerage"
    assert exc.value.bound == "demo"
    # The denial landed exactly one security event on the existing ledger seam.
    assert [r.integration for r in audit.records] == ["security"]
    assert audit.records[0].args == {
        "attempted_tenant": "other-brokerage",
        "bound_tenant": "demo",
    }


async def test_foreign_tenant_nested_in_a_list_response_is_blocked() -> None:
    with tenant_scope("demo"):
        with pytest.raises(EgressBlockedError):
            await filter_tool_response(
                "list_active_transactions",
                [_txn("demo"), _txn("other-brokerage")],
                recipient_role=Role.OPERATOR,
            )


async def test_no_bound_tenant_fails_closed_for_a_tenant_stamped_response() -> None:
    # An empty stamped tenant_id still runs enforce_tenant("") — a response can never
    # egress with no TenantContext bound (the input side's fail-closed rule, mirrored).
    with pytest.raises(TenantContextMissing):
        await filter_tool_response("get_transaction", _txn(""), recipient_role=Role.OPERATOR)


async def test_block_logs_tool_and_finding_class_but_never_the_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with tenant_scope("demo"), caplog.at_level(logging.WARNING):
        with pytest.raises(EgressBlockedError):
            await filter_tool_response(
                "get_transaction", _txn("other-brokerage"), recipient_role=Role.OPERATOR
            )
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("get_transaction" in m and "cross_tenant_identifier" in m for m in messages)
    # No payload content in any log line: not the foreign tenant id, not a field value.
    assert all("other-brokerage" not in m and "L1" not in m for m in messages)


async def test_matching_and_ambient_tenant_responses_pass_through_unchanged() -> None:
    with tenant_scope("demo"):
        stamped = _txn("demo")
        assert await filter_tool_response("get", stamped, recipient_role=Role.OPERATOR) is stamped
        ambient = _txn("")
        assert await filter_tool_response("get", ambient, recipient_role=Role.OPERATOR) is ambient


# ---- rule 2: secret shapes ⇒ REDACT in place ---------------------------------------------


class _AdapterStatus(BaseModel):
    """A response model with a secret-named field (rule 2a on model fields)."""

    name: str
    access_token: str = ""


def test_secret_named_model_field_is_redacted_with_the_marker() -> None:
    scrubbed = scrub_payload(
        _AdapterStatus(name="crm", access_token="abc123"), recipient_role=Role.ADMIN
    )
    assert scrubbed.access_token == SECRET_REDACTED
    assert scrubbed.name == "crm"


def test_secret_named_mapping_key_is_redacted_deeply() -> None:
    original: dict[str, Any] = {"contact_id": "42", "nested": {"api_key": "abc123", "name": "ok"}}
    scrubbed = scrub_payload(original, recipient_role=Role.ADMIN)
    assert scrubbed["nested"]["api_key"] == SECRET_REDACTED
    assert scrubbed["nested"]["name"] == "ok"
    # The input is never mutated (pure / copy-on-write).
    assert original["nested"]["api_key"] == "abc123"


def test_connection_string_span_is_redacted_inside_free_text() -> None:
    body = "reach the db at postgres://svc:hunter2@db.internal:5432/app then restart"
    scrubbed = scrub_payload(body, recipient_role=Role.ADMIN)
    # Only the credential span is replaced; the surrounding text survives.
    assert SECRET_REDACTED in scrubbed
    assert "hunter2" not in scrubbed
    assert scrubbed.startswith("reach the db at ")
    assert scrubbed.endswith("db.internal:5432/app then restart")


def test_bearer_and_jwt_and_key_prefix_shapes_are_redacted() -> None:
    # Fixture values are built to match the egress patterns while staying obviously fake.
    bearer = "Authorization was Bearer " + "aaaabbbbccccdddd1111"
    jwt_like = "sig " + "eyJhbGciOiJI" + "." + "eyJzdWIiOiJk" + "." + "c2lnbmF0dXJl"
    key = "the key sk-" + "aaaabbbbccccdddd" + " leaked"
    for text in (bearer, jwt_like, key):
        scrubbed = scrub_payload(text, recipient_role=Role.ADMIN)
        assert SECRET_REDACTED in scrubbed, text


def test_private_key_block_is_redacted() -> None:
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----\nnot-a-real-key\n-----END " + "RSA PRIVATE KEY-----"
    scrubbed = scrub_payload(f"attached:\n{pem}\ndone", recipient_role=Role.ADMIN)
    assert scrubbed == f"attached:\n{SECRET_REDACTED}\ndone"


def test_plain_business_text_is_untouched_and_identity_preserved() -> None:
    remarks = "Charming 3bd/2ba near the park; seller motivated. https://tour.example.com/L1"
    assert scrub_payload(remarks, recipient_role=Role.VIEWER) is remarks


# ---- rule 3: role-restricted PII ⇒ REDACT for an unentitled recipient --------------------


def test_contact_pii_is_redacted_for_a_viewer_recipient() -> None:
    contact = _contact()
    scrubbed = scrub_payload(contact, recipient_role=Role.VIEWER)
    assert scrubbed.email == PII_REDACTED
    assert scrubbed.phone == PII_REDACTED
    assert scrubbed.name == "Jane Buyer"  # only the annotated fields are touched
    # Original untouched.
    assert contact.email == "jane@example.com"


def test_contact_pii_passes_through_for_operator_and_admin() -> None:
    contact = _contact()
    assert scrub_payload(contact, recipient_role=Role.OPERATOR) is contact
    assert scrub_payload(contact, recipient_role=Role.ADMIN) is contact


def test_message_recipient_is_redacted_for_a_viewer() -> None:
    message = Message(id="MSG-1", recipient="jane@example.com", subject="hi", body="hello")
    scrubbed = scrub_payload(message, recipient_role=Role.VIEWER)
    assert scrubbed.recipient == PII_REDACTED
    assert scrubbed.body == "hello"


def test_non_string_pii_field_is_dropped_not_marked() -> None:
    class _Report(BaseModel):
        label: str
        ages: Annotated[list[int] | None, Pii(min_role=Role.ADMIN)] = None

    scrubbed = scrub_payload(_Report(label="x", ages=[41, 52]), recipient_role=Role.OPERATOR)
    assert scrubbed.ages is None
    assert scrubbed.label == "x"


def test_pii_annotation_rides_on_the_model_not_the_tool() -> None:
    # The rule is data on the field: any container of the model inherits it with no
    # per-tool code.
    contacts = [_contact(), _contact()]
    scrubbed = scrub_payload(contacts, recipient_role=Role.VIEWER)
    assert [c.email for c in scrubbed] == [PII_REDACTED, PII_REDACTED]


def test_annotated_marker_is_importable_rule_data() -> None:
    assert CONTACT_PII.min_role is Role.OPERATOR
    assert Role.OPERATOR.allows(CONTACT_PII.min_role)
    assert not Role.VIEWER.allows(CONTACT_PII.min_role)


# ---- the seam wrapper: same shape as authorize_tool_ports, output direction --------------


class _LeakyStore:
    """A port whose inner layers failed to confine a row — egress is the last belt."""

    def __init__(self, row: Transaction) -> None:
        self._row = row

    async def get_transaction(self, transaction_id: str) -> Transaction | None:
        return self._row

    async def get_status(self) -> dict[str, Any]:
        return {"api_key": "abc123", "healthy": True}


async def test_wrapped_port_blocks_a_foreign_response_before_the_caller_sees_it() -> None:
    audit = _CollectingAudit()
    store = filter_port_egress(
        _LeakyStore(_txn("other-brokerage")), recipient_role=Role.OPERATOR, audit=audit
    )
    with tenant_scope("demo"):
        with pytest.raises(EgressBlockedError):
            await store.get_transaction("TXN-1")
    # The caller received the failure, and the denial is on the security ledger.
    assert [r.integration for r in audit.records] == ["security"]


async def test_wrapped_port_redacts_a_secret_bearing_response() -> None:
    store = filter_port_egress(_LeakyStore(_txn("demo")), recipient_role=Role.OPERATOR)
    with tenant_scope("demo"):
        status = await store.get_status()
    assert status == {"api_key": SECRET_REDACTED, "healthy": True}


def test_filter_port_egress_is_marked_and_idempotent() -> None:
    store = _LeakyStore(_txn("demo"))
    once = filter_port_egress(store)
    assert getattr(once, EGRESS_FILTERED_MARKER, False) is True
    assert getattr(once.get_transaction, EGRESS_FILTERED_MARKER, False) is True
    # Re-wrapping is a no-op (same instance, methods not double-wrapped).
    assert filter_port_egress(once) is once


def test_guard_tool_ports_covers_both_directions_with_both_markers() -> None:
    # The one-wrapper composition the api wiring uses: BOP-011 authorization inbound,
    # BOP-012 egress filtering outbound, same instance back, both markers detectable
    # (the egress marker is what BOP-020's wiring gate checks for).
    store = guard_tool_ports(_LeakyStore(_txn("demo")))
    assert getattr(store, AUTHORIZED_MARKER, False) is True
    assert getattr(store, EGRESS_FILTERED_MARKER, False) is True


async def test_guarded_port_still_authorizes_inputs_and_filters_outputs() -> None:
    audit = _CollectingAudit()
    store = guard_tool_ports(_LeakyStore(_txn("other-brokerage")), audit=audit)
    with tenant_scope("demo"):
        # Output direction: the leaked foreign row is blocked at egress.
        with pytest.raises(EgressBlockedError):
            await store.get_transaction("TXN-1")
    assert [r.integration for r in audit.records] == ["security"]


def test_self_referential_payload_terminates() -> None:
    payload: dict[str, Any] = {"name": "loop"}
    payload["self"] = payload
    scrubbed = scrub_payload(payload, recipient_role=Role.ADMIN)
    assert scrubbed["name"] == "loop"
