"""Outbound business-comms routes (BOP-015/018, ADR-0015).

POST /messages/send drives the full seam for both channels: template render →
`outbound_messages` row → a channel-port send (EmailPort or SMSPort, picked by
`channel`) that is deduped (ADR-0011) and recorded in the audit ledger
(ADR-0010), tenant-scoped throughout (ADR-0012). The route binds the run context
(`audit_scope`) the seam decorators read: a caller-supplied `request_id` makes a
retried request the *same* logical send — replays return the original message
without emailing twice — while omitting it makes each request a fresh send.

Note the at-most-once posture (ADR-0011) cuts both ways: a FAILED send is not
retryable under the same `request_id` (its idempotency claim stays pending → a
permanent 409); recovery is a new `request_id`, i.e. a new logical send.
"""

import re
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from brokerops_api.deps import (
    get_approval_repo,
    get_message_service,
    get_message_store,
    require_role,
)
from brokerops_api.db import ApprovalRepo
from brokerops_api.routes._egress import ScrubDep
from brokerops_api.routes.approvals import APPROVE_OUTBOUND_MESSAGE
from brokerops_core.models.message import Message, MessageChannel, MessageStatus
from brokerops_core.models.message_templates import TemplateParamError, UnknownTemplateError
from brokerops_core.ports.identity import Principal, Role
from brokerops_core.ports.messaging import MessageStore
from brokerops_core.services.audit import AuditContext, audit_scope
from brokerops_core.services.idempotency import ReplayInProgressError
from brokerops_core.services.message_send import MessageSendService, UnknownOutboundMessageError

router = APIRouter(prefix="/messages", tags=["messages"])

ServiceDep = Annotated[MessageSendService, Depends(get_message_service)]
StoreDep = Annotated[MessageStore, Depends(get_message_store)]
ApprovalRepoDep = Annotated[ApprovalRepo, Depends(get_approval_repo)]
# Sending a client-facing email is an action — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]
# Reads are open to any authenticated role; the response is caller-role filtered via
# the shared egress seam (BOP-040/ScrubDep) so the freeform body and recipient are
# redacted for viewers — a viewer-open surface never receives the message text.
# Retrying a FAILED send re-drives an already-approved decision — admins only,
# like the decide route it completes (BOP-037).
AdminDep = Annotated[Principal, Depends(require_role(Role.ADMIN))]

# E.164: "+" then 2–15 digits, no leading zero. Validated at the boundary so a
# garbage recipient is a 422, not a billed provider round-trip (BOP-037).
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


class SendMessageRequest(BaseModel):
    # Which port the message leaves through: email (default) or sms (BOP-018).
    channel: MessageChannel = MessageChannel.EMAIL
    # email address or E.164 phone number, per channel.
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

    @model_validator(mode="after")
    def _sms_recipient_is_e164(self) -> "SendMessageRequest":
        if self.channel is MessageChannel.SMS and not _E164.fullmatch(self.recipient):
            raise ValueError("sms recipient must be an E.164 phone number (e.g. +15551230101)")
        return self


@router.post("/send", status_code=201)
async def send_message(
    body: SendMessageRequest, service: ServiceDep, principal: OperatorDep
) -> Message:
    context = AuditContext(
        workflow_run_id=body.request_id or uuid4().hex,
        workflow="message_send",
        actor=principal.email,
        # Tags this direct send's ledger entry with the deal (BOP-027) so it joins
        # the transaction's audit slice like a workflow-driven send does.
        transaction_id=body.transaction_id,
    )
    send = service.send_sms if body.channel is MessageChannel.SMS else service.send_email
    with audit_scope(context):
        try:
            return await send(
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


@router.post("/{message_id}/retry")
async def retry_failed_message(
    message_id: str,
    service: ServiceDep,
    repo: ApprovalRepoDep,
    principal: AdminDep,
) -> Message:
    """Re-drive a FAILED send to completion (BOP-037).

    The gap this closes: an approved gate whose provider send failed leaves the
    approval APPROVED (the decide route 409s forever) and the row FAILED — with
    no surface able to reach `send_approved`'s FAILED→SENT path. Strictly
    FAILED-only, so it can never bypass a pending human gate or re-send a
    terminal row; the send runs through the same seam (audited + deduped) under
    a fresh run id, linked back to the original approval when one exists.
    """
    row = await service.get_message(message_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"message {message_id!r} not found")
    if row.status is not MessageStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"only a failed message can be retried (this one is {row.status.value})",
        )
    # Audit linkage: the approval that originally approved this message, if the
    # row came through the gate (a direct /messages/send failure has none).
    approvals = await repo.list(None)
    original = next(
        (
            a
            for a in approvals
            if a.kind == APPROVE_OUTBOUND_MESSAGE
            and (a.payload or {}).get("message_id") == message_id
        ),
        None,
    )
    context = AuditContext(
        # A fresh run id: the failed attempt's idempotency claim is still pending
        # under its original run, and a retry is a genuinely new logical send.
        workflow_run_id=uuid4().hex,
        workflow="message_retry",
        approval_id=original.id if original is not None else None,
        actor=principal.email,
        # The retried send stays attributed to the same deal (BOP-027).
        transaction_id=row.transaction_id,
    )
    with audit_scope(context):
        try:
            return await service.send_approved(message_id)
        except UnknownOutboundMessageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReplayInProgressError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            # The provider failed again: the row stays FAILED (and the attempt is
            # in the audit ledger) — surface it as an upstream failure, retryable.
            raise HTTPException(status_code=502, detail=f"provider send failed: {exc}") from exc


@router.get("/{message_id}")
async def get_message(message_id: str, store: StoreDep, scrub: ScrubDep) -> Message:
    message = await store.get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail=f"message {message_id!r} not found")
    return scrub(message)


@router.get("")
async def list_messages(
    store: StoreDep,
    scrub: ScrubDep,
    contact_id: Annotated[str | None, Query()] = None,
    transaction_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[Message]:
    """The outbound comms history, newest first — read-open like the audit trail.

    `transaction_id` scopes the history to one deal for the transaction hub
    (BOP-027); both filters compose (AND) when supplied together. The response is
    caller-role filtered (BOP-040): the freeform body/subject and the recipient
    (contact PII) are redacted for viewers so a viewer-open hub never receives
    message content over the wire.
    """
    rows = await store.list_messages(contact_id, limit, transaction_id)
    return scrub(rows)
