"""End-to-end outbound-SMS flow through the real lifespan wiring (BOP-018).

`with TestClient(app)` runs main.py's lifespan, so these tests exercise exactly
what `docker compose up` runs in database-less mode: the Twilio-shaped stub over
the `internal` sentinel with zero credentials, the seam decorators
(IdempotentSMS → RecordingSMS), the tenant-scoped in-memory message store, the
/messages routes, and the fail-closed /webhooks/twilio-sms delivery callback.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from brokerops_api.main import app
from brokerops_twilio_sms.signature import compute_signature

PAYLOAD: dict[str, Any] = {
    "channel": "sms",
    "recipient": "+15551230101",
    "template": "showing_followup_sms:v1",
    "params": {
        "recipient_name": "Sam",
        "listing_address": "412 Alder Court",
        "sender_name": "The Rivermouth Team",
    },
    "contact_id": "101",
    "listing_key": "RM1001",
}

DEMO_TOKEN = "demo-twilio-token"
# TestClient requests carry this base URL; the webhook signs/validates against it.
WEBHOOK_URL = "http://testserver/webhooks/twilio-sms"


@pytest.fixture(autouse=True)
def webhook_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DEMO_TOKEN)
    monkeypatch.delenv("TWILIO_STATUS_CALLBACK_URL", raising=False)
    monkeypatch.delenv("SMS_PROVIDER", raising=False)
    monkeypatch.delenv("SMS_BASE_URL", raising=False)


def _signed_callback(
    client: TestClient,
    params: dict[str, str],
    token: str = DEMO_TOKEN,
    signature: str | None = None,
) -> Any:
    if signature is None:
        signature = compute_signature(token, WEBHOOK_URL, params)
    return client.post(
        "/webhooks/twilio-sms", data=params, headers={"X-Twilio-Signature": signature}
    )


def test_stub_send_persists_sms_message_and_lands_in_the_audit_ledger() -> None:
    with TestClient(app) as client:
        sent = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-sms-audit-1"})
        assert sent.status_code == 201
        message = sent.json()
        assert message["channel"] == "sms"
        assert message["status"] == "sent"
        assert message["provider_message_id"].startswith("SM")
        assert message["subject"] == ""  # SMS has no subject line
        assert "412 Alder Court" in message["body"]
        assert message["tenant_id"] == "demo"

        # Persisted to the same outbound_messages history as email.
        fetched = client.get(f"/messages/{message['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == message

        # The send crossed the provider boundary once — and the audit ledger saw it.
        trail = client.get("/audit?workflow_run_id=req-sms-audit-1").json()
        assert len(trail) == 1
        record = trail[0]
        assert record["integration"] == "twilio_sms"
        assert record["tool"] == "send_sms"
        assert record["outcome"] == "success"
        assert record["external_id"] == message["provider_message_id"]


def test_replay_with_the_same_request_id_dedupes() -> None:
    with TestClient(app) as client:
        first = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-sms-r1"}).json()
        again = client.post("/messages/send", json={**PAYLOAD, "request_id": "req-sms-r1"}).json()
        # Same logical send: same row, same provider id — no second text.
        assert again["id"] == first["id"]
        assert again["provider_message_id"] == first["provider_message_id"]
        history = client.get("/messages?contact_id=101").json()
        assert len([m for m in history if m["id"] == first["id"]]) == 1
        # And no second mutation record (idempotency wraps recording).
        trail = client.get("/audit?workflow_run_id=req-sms-r1").json()
        assert len(trail) == 1


def _send_one(client: TestClient, request_id: str) -> dict[str, Any]:
    response = client.post("/messages/send", json={**PAYLOAD, "request_id": request_id})
    assert response.status_code == 201
    result: dict[str, Any] = response.json()
    return result


def test_delivery_webhook_fails_closed_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = {"MessageSid": "SMwhatever", "MessageStatus": "delivered"}
    with TestClient(app, raise_server_exceptions=False) as client:
        for broken in ("", "unset"):
            monkeypatch.setenv("TWILIO_AUTH_TOKEN", broken)
            response = client.post(
                "/webhooks/twilio-sms",
                data=params,
                headers={"X-Twilio-Signature": compute_signature("x", WEBHOOK_URL, params)},
            )
            assert response.status_code == 500
            assert "TWILIO_AUTH_TOKEN" in response.json()["detail"]


def test_delivery_webhook_rejects_bad_or_missing_signature() -> None:
    with TestClient(app) as client:
        message = _send_one(client, "req-sms-sig-1")
        params = {"MessageSid": message["provider_message_id"], "MessageStatus": "delivered"}
        unsigned = client.post("/webhooks/twilio-sms", data=params)
        assert unsigned.status_code == 401
        forged = _signed_callback(client, params, signature="forged==")
        assert forged.status_code == 401
        wrong_key = _signed_callback(client, params, token="not-the-account-token")
        assert wrong_key.status_code == 401
        # Signature over different params than the ones posted → invalid.
        tampered = client.post(
            "/webhooks/twilio-sms",
            data={**params, "MessageStatus": "failed"},
            headers={"X-Twilio-Signature": compute_signature(DEMO_TOKEN, WEBHOOK_URL, params)},
        )
        assert tampered.status_code == 401
        # Nothing above touched the row.
        assert client.get(f"/messages/{message['id']}").json()["status"] == "sent"


def test_valid_delivered_callback_transitions_sent_to_delivered() -> None:
    with TestClient(app) as client:
        message = _send_one(client, "req-sms-del-1")
        response = _signed_callback(
            client,
            {"MessageSid": message["provider_message_id"], "MessageStatus": "delivered"},
        )
        assert response.status_code == 200
        assert response.json() == {"processed": True, "id": message["id"], "status": "delivered"}
        assert client.get(f"/messages/{message['id']}").json()["status"] == "delivered"


def test_valid_failed_callback_transitions_sent_to_failed() -> None:
    with TestClient(app) as client:
        message = _send_one(client, "req-sms-fail-1")
        for twilio_status in ("failed", "undelivered"):
            response = _signed_callback(
                client,
                {"MessageSid": message["provider_message_id"], "MessageStatus": twilio_status},
            )
            assert response.status_code == 200
        assert client.get(f"/messages/{message['id']}").json()["status"] == "failed"


def test_callbacks_never_move_the_lifecycle_backwards() -> None:
    with TestClient(app) as client:
        message = _send_one(client, "req-sms-order-1")
        sid = message["provider_message_id"]
        delivered = _signed_callback(client, {"MessageSid": sid, "MessageStatus": "delivered"})
        assert delivered.json()["status"] == "delivered"
        # A late "sent" callback (Twilio guarantees no ordering) must not downgrade.
        late = _signed_callback(client, {"MessageSid": sid, "MessageStatus": "sent"})
        assert late.status_code == 200
        assert late.json()["status"] == "delivered"
        assert client.get(f"/messages/{message['id']}").json()["status"] == "delivered"


def test_unknown_sid_and_preacceptance_statuses_are_acknowledged_and_ignored() -> None:
    with TestClient(app) as client:
        unknown = _signed_callback(client, {"MessageSid": "SMnobody", "MessageStatus": "delivered"})
        assert unknown.status_code == 200
        assert unknown.json()["ignored"] is True
        message = _send_one(client, "req-sms-queued-1")
        queued = _signed_callback(
            client,
            {"MessageSid": message["provider_message_id"], "MessageStatus": "queued"},
        )
        assert queued.status_code == 200
        assert queued.json()["ignored"] is True
        assert client.get(f"/messages/{message['id']}").json()["status"] == "sent"


def test_callback_naming_a_rejected_messages_sid_is_a_clean_noop() -> None:
    # BOP-019 weave: REJECTED is a terminal human "no" with a STATUS_RANK entry —
    # a stray delivery callback naming such a row's sid must be acknowledged as a
    # no-op (rank check), never KeyError into a 500 or advance the row.
    import asyncio

    from brokerops_core.models.message import Message, MessageChannel, MessageStatus
    from brokerops_core.services.tenancy import tenant_scope

    with TestClient(app) as client:
        rejected = Message(
            id="msg-rejected-1",
            channel=MessageChannel.SMS,
            recipient="+15551230101",
            body="drafted then rejected",
            status=MessageStatus.REJECTED,
            provider_message_id="SMrejected1",
        )

        async def save() -> None:
            with tenant_scope("demo"):
                await app.state.message_store.save_message(rejected)

        asyncio.run(save())
        response = _signed_callback(
            client, {"MessageSid": "SMrejected1", "MessageStatus": "delivered"}
        )
        assert response.status_code == 200
        assert response.json() == {
            "processed": True,
            "id": "msg-rejected-1",
            "status": "rejected",
        }
        assert client.get("/messages/msg-rejected-1").json()["status"] == "rejected"
