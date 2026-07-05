"""Inbound webhooks.

Vapi posts an end-of-call-report when a call finishes; we verify the shared
secret (x-vapi-secret), normalize, and dispatch one vapi_followup graph run.
Non-end-of-call messages are acknowledged and ignored.

Twilio posts SMS delivery-status callbacks (BOP-018); we verify the
X-Twilio-Signature (HMAC over the callback URL + params, keyed by the account
auth token) and transition the `outbound_messages` row. Both webhooks fail
closed: no configured secret means no accepted callback, ever.
"""

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from brokerops_api.deps import get_message_store, get_workflow_engine
from brokerops_api.workflows import VAPI_FOLLOWUP, WorkflowEngine
from brokerops_core.models.message import MessageStatus
from brokerops_core.ports.messaging import MessageStore
from brokerops_twilio_sms.signature import signature_is_valid

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]
MessageStoreDep = Annotated[MessageStore, Depends(get_message_store)]


@router.post("/vapi")
async def vapi_webhook(
    request: Request,
    engine: EngineDep,
    x_vapi_secret: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    # Fail closed: this webhook starts a workflow run, so it must never accept an
    # unauthenticated POST. An empty secret — or the Terraform "unset" placeholder, which
    # is repo-known and so worthless as a shared secret — means the deploy is
    # misconfigured, not that auth is "off". Every supported path supplies a real value
    # (compose sets a demo secret; TF generates one that the in-process stub and real Vapi
    # both send), so a 500 surfaces the misconfiguration instead of opening the endpoint.
    secret = os.environ.get("VAPI_WEBHOOK_SECRET", "")
    if not secret or secret == "unset":
        raise HTTPException(status_code=500, detail="VAPI_WEBHOOK_SECRET is not configured")
    if x_vapi_secret != secret:
        raise HTTPException(status_code=401, detail="bad or missing x-vapi-secret")

    body = await request.json()
    message = body.get("message") or {}
    if message.get("type") != "end-of-call-report":
        return {"ignored": True, "type": message.get("type")}

    call = message.get("call") or {}
    metadata = call.get("metadata") or {}
    artifact = message.get("artifact") or {}
    run = await engine.start(
        VAPI_FOLLOWUP,
        {
            "call_id": str(call.get("id", "")),
            "listing_key": str(metadata.get("listing_key", "")),
            "contact_id": str(metadata.get("contact_id", "")),
            "transcript": artifact.get("transcript", ""),
            "call_outcome": message.get("endedReason", "completed"),
        },
    )
    return {
        "processed": True,
        "thread_id": run.thread_id,
        "status": run.status,
        "approval_id": run.approval.id if run.approval else None,
    }


# Twilio MessageStatus values → our lifecycle. Pre-acceptance states (queued,
# accepted, sending) are acknowledged and ignored — SENT was already persisted
# by the send path when the provider accepted the message.
_TWILIO_STATUS_MAP = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "failed": MessageStatus.FAILED,
    "undelivered": MessageStatus.FAILED,
}


@router.post("/twilio-sms")
async def twilio_sms_webhook(
    request: Request,
    store: MessageStoreDep,
    x_twilio_signature: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    # Fail closed (the BOP-007 posture): this webhook mutates the comms history, so
    # it must never accept an unauthenticated POST. Twilio signs callbacks with the
    # account auth token; an empty token — or the Terraform "unset" placeholder,
    # which is repo-known and so worthless as a signing key — means the deploy is
    # misconfigured, not that auth is "off": a 500 surfaces it instead of opening
    # the endpoint.
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token or auth_token == "unset":
        raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN is not configured")
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    # The signature covers the URL exactly as Twilio requested it. Behind a proxy
    # (Cloud Run) the URL the app sees can differ from the public one, so the
    # deploy pins the canonical callback URL; unset falls back to the request URL
    # (correct for direct/compose deploys).
    url = os.environ.get("TWILIO_STATUS_CALLBACK_URL", "") or str(request.url)
    if not signature_is_valid(auth_token, url, params, x_twilio_signature or ""):
        raise HTTPException(status_code=401, detail="bad or missing X-Twilio-Signature")

    message_sid = params.get("MessageSid", "")
    twilio_status = params.get("MessageStatus", "")
    status = _TWILIO_STATUS_MAP.get(twilio_status)
    if status is None:
        return {"ignored": True, "message_status": twilio_status}
    message = await store.get_message_by_provider_id(message_sid)
    if message is None:
        # Unknown sid (or another tenant's — the scoped store resolves those to
        # None). 200 so Twilio doesn't retry a callback we will never match.
        return {"ignored": True, "reason": "unknown MessageSid"}
    # Callbacks carry no ordering guarantee: only ever move the lifecycle forward.
    # The rank check-and-write is atomic AT THE STORE (BOP-037) — a read-side
    # guard here would let two concurrent callbacks for one sid both pass and
    # last-writer-win; the conditional transition also writes only the status
    # column, so it can't clobber concurrent field changes the way the old
    # full-row save could. A late "sent" still never downgrades DELIVERED/FAILED.
    row = await store.advance_message_status(message.id, status)
    final = row if row is not None else message
    return {"processed": True, "id": message.id, "status": final.status.value}
