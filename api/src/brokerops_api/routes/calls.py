import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brokerops_api.deps import get_crm_port, get_feedback_store, get_voice_port, require_role
from brokerops_core.models.call import CallRecord
from brokerops_core.models.feedback import ShowingFeedback
from brokerops_core.ports.crm import CRMPort
from brokerops_core.ports.feedback import FeedbackStore
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.voice import VoicePort

router = APIRouter(tags=["calls"])

VoiceDep = Annotated[VoicePort, Depends(get_voice_port)]
CRMDep = Annotated[CRMPort, Depends(get_crm_port)]
FeedbackDep = Annotated[FeedbackStore, Depends(get_feedback_store)]
# Placing an outbound call is an action — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]


class OutboundCallRequest(BaseModel):
    listing_key: str
    contact_id: str
    scenario: str | None = None  # demo-stub hint only; ignored by real Vapi


class OutboundCallResult(BaseModel):
    call_id: str
    status: str = "queued"


@router.post("/calls/outbound", status_code=202)
async def start_outbound_call(
    body: OutboundCallRequest, voice: VoiceDep, crm: CRMDep, principal: OperatorDep
) -> OutboundCallResult:
    contact = await crm.get_contact(body.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail=f"contact {body.contact_id!r} not found")
    context: dict[str, str] = {"listing_key": body.listing_key}
    if contact.phone:
        context["phone"] = contact.phone
    if body.scenario:
        context["scenario"] = body.scenario
    assistant_id = os.environ.get("VAPI_ASSISTANT_ID", "demo-assistant")
    call_id = await voice.start_outbound_call(body.contact_id, assistant_id, context)
    return OutboundCallResult(call_id=call_id)


@router.get("/calls/{call_id}")
async def get_call_record(call_id: str, store: FeedbackDep) -> CallRecord:
    record = await store.get_call_record(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"call {call_id!r} not recorded")
    return record


@router.get("/feedback")
async def list_feedback(listing_key: str, store: FeedbackDep) -> list[ShowingFeedback]:
    return await store.list_feedback(listing_key)
