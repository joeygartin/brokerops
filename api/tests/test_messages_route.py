"""End-to-end outbound-email flow through the real lifespan wiring (BOP-015).

`with TestClient(app)` runs main.py's lifespan, so these tests exercise exactly what
`docker compose up` runs in database-less mode: the stub provider over the `internal`
sentinel, the seam decorators (IdempotentEmail → RecordingEmail), the tenant-scoped
in-memory message store, and the /messages routes — zero credentials anywhere.
"""

from typing import Any

from fastapi.testclient import TestClient

from brokerops_api.main import app

PAYLOAD: dict[str, Any] = {
    "recipient": "sam@example.com",
    "template": "showing_followup:v1",
    "params": {
        "recipient_name": "Sam",
        "listing_address": "412 Alder Court",
        "sender_name": "The Rivermouth Team",
    },
    "contact_id": "101",
    "listing_key": "RM1001",
}


def test_stub_send_persists_sent_message_and_lands_in_the_audit_ledger() -> None:
    with TestClient(app) as client:
        sent = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-audit-1"})
        assert sent.status_code == 201
        message = sent.json()
        assert message["status"] == "sent"
        assert message["provider_message_id"].startswith("stub-email-")
        assert message["template_ref"] == "showing_followup:v1"
        assert message["subject"] == "Following up on your tour of 412 Alder Court"
        # The send response is the stored row: tenant stamped by the scoped store
        # (ADR-0012), identical to what the history returns.
        assert message["tenant_id"] == "demo"

        # Persisted to the comms history and readable back.
        fetched = client.get(f"/messages/{message['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == message

        # The send crossed the provider boundary once — and the audit ledger saw it.
        trail = client.get("/audit?workflow_run_id=req-audit-1").json()
        assert len(trail) == 1
        record = trail[0]
        assert record["integration"] == "email"
        assert record["tool"] == "send_email"
        assert record["outcome"] == "success"
        assert record["external_id"] == message["provider_message_id"]
        assert record["actor"] == "operator@demo.brokerops"


def test_replay_with_the_same_request_id_dedupes() -> None:
    with TestClient(app) as client:
        first = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-replay-1"}).json()
        again = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-replay-1"}).json()

        # Same logical send: same row, same provider id — no second email.
        assert again["id"] == first["id"]
        assert again["provider_message_id"] == first["provider_message_id"]
        history = client.get("/messages?contact_id=101").json()
        assert len([m for m in history if m["id"] == first["id"]]) == 1
        # And no second mutation record (idempotency wraps recording).
        trail = client.get("/audit?workflow_run_id=req-replay-1").json()
        assert len(trail) == 1


def test_distinct_request_ids_are_distinct_sends() -> None:
    with TestClient(app) as client:
        first = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-a"}).json()
        second = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-b"}).json()
        assert first["id"] != second["id"]
        assert first["provider_message_id"] != second["provider_message_id"]


def test_template_errors_are_422() -> None:
    with TestClient(app) as client:
        unknown = client.post("/messages/send", json={**PAYLOAD, "template": "showing_followup:v9"})
        assert unknown.status_code == 422
        assert "unknown message template" in unknown.json()["detail"]
        missing = client.post("/messages/send", json={**PAYLOAD, "params": {}})
        assert missing.status_code == 422
        assert "requires parameter" in missing.json()["detail"]


def test_missing_message_is_404() -> None:
    with TestClient(app) as client:
        assert client.get("/messages/nope").status_code == 404
