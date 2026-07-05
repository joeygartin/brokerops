"""Outbound business-email routes (BOP-015, ADR-0015).

POST /messages/send drives the full seam: template render → `outbound_messages`
row → EmailPort send that is deduped (ADR-0011) and recorded in the audit ledger
(ADR-0010), tenant-scoped throughout (ADR-0012). The route binds the run context
(`audit_scope`) the seam decorators read: a caller-supplied `request_id` makes a
retried request the *same* logical send — replays return the original message
without emailing twice — while omitting it makes each request a fresh send.

Note the at-most-once posture (ADR-0011) cuts both ways: a FAILED send is not
retryable under the same `request_id` (its idempotency claim stays pending → a
permanent 409); recovery is a new `request_id`, i.e. a new logical send.
"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from brokerops_api.deps import get_message_service, get_message_store, require_role
from brokerops_core.models.message import Message
from brokerops_core.models.message_templates import TemplateParamError, UnknownTemplateError
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.messaging import MessageStore
from brokerops_core.services.audit import AuditContext, audit_scope
from brokerops_core.services.idempotency import ReplayInProgressError
from brokerops_core.services.message_send import MessageSendService

router = APIRouter(prefix="/messages", tags=["messages"])

ServiceDep = Annotated[MessageSendService, Depends(get_message_service)]
StoreDep = Annotated[MessageStore, Depends(get_message_store)]
# Sending a client-facing email is an action — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]


class SendMessageRequest(BaseModel):
    recipient: str
    # Versioned template ref, e.g. "showing_followup:v1" (templates are source, ADR-0005).
    template: str
    params: dict[str, str] = Field(default_factory=dict)
    contact_id: str = ""
    listing_key: str = ""
    transaction_id: str = ""
    # Optional idempotency token: retries carrying the same request_id are one
    # logical send (deduped); omitted → every request is a fresh send.
    request_id: str = ""


@router.post("/send", status_code=201)
async def send_message(
    body: SendMessageRequest, service: ServiceDep, principal: OperatorDep
) -> Message:
    context = AuditContext(
        workflow_run_id=body.request_id or uuid4().hex,
        workflow="message_send",
        actor=principal.email,
    )
    with audit_scope(context):
        try:
            return await service.send_email(
                recipient=body.recipient,
                template_ref=body.template,
                params=body.params,
                contact_id=body.contact_id,
                listing_key=body.listing_key,
                transaction_id=body.transaction_id,
            )
        except (UnknownTemplateError, TemplateParamError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ReplayInProgressError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{message_id}")
async def get_message(message_id: str, store: StoreDep) -> Message:
    message = await store.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=f"message {message_id!r} not found")
    return message


@router.get("")
async def list_messages(
    store: StoreDep,
    contact_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[Message]:
    """The outbound comms history, newest first — read-open like the audit trail."""
    return await store.list_messages(contact_id, limit)
