"""Inbound webhooks.

Vapi posts an end-of-call-report when a call finishes; we verify the shared
secret (x-vapi-secret), normalize, and dispatch one vapi_followup graph run.
Non-end-of-call messages are acknowledged and ignored.
"""

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from brokerops_api.deps import get_workflow_engine
from brokerops_api.workflows import VAPI_FOLLOWUP, WorkflowEngine

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]


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
