"""Deterministic drafting + the drafted-touchpoint rules (BOP-019).

`DeterministicDrafter` is the zero-credential DraftingPort default: it renders
the versioned core templates (ADR-0005) verbatim — a pure function of the
context, so the whole draft → approve → send spine ships and is testable before
any LLM backend (BOP-020) exists.

The `plan_*` functions are the business rules for *when* a workflow drafts and
*who* receives it — they live here (not in nodes) by architecture rule #3.
Returning None means "no recipient on file → no draft": the workflow keeps its
existing CRM-task behavior and simply skips the tail.
"""

from collections.abc import Mapping
from typing import Any

from brokerops_core.models.contact import Contact
from brokerops_core.models.drafting import DraftContext, DraftedMessage
from brokerops_core.models.message_templates import SHOWING_FOLLOWUP_V1, get_template

# Signature line for deterministically drafted comms. Deploy-configurable
# sender identity can come later without touching the rules that use it.
SENDER_NAME = "The Brokerage Team"


class DeterministicDrafter:
    """Template-rendering DraftingPort default (zero credential).

    Raises UnknownTemplateError / TemplateParamError rather than producing a
    half-filled draft — the same fail-loud posture as direct sends.
    """

    async def draft(self, context: DraftContext) -> DraftedMessage:
        template = get_template(context.template_ref)
        subject, body = template.render(context.params)
        return DraftedMessage(
            channel=context.channel,
            recipient=context.recipient,
            subject=subject,
            body=body,
            template_ref=template.ref,
            contact_id=context.contact_id,
            listing_key=context.listing_key,
            transaction_id=context.transaction_id,
        )


def edited_draft_fields(edited: Mapping[str, Any] | None) -> tuple[str, str]:
    """Extract the approver's (subject, body) edits from a decision's edited_payload.

    "" means "not edited — keep the draft". A present-but-blank body is refused
    loudly: the inbox card promises the visible text is exactly what sends, so an
    empty edit must never silently fall back to the original draft — the right
    move for "don't send this" is rejecting the gate. Both engines' gate nodes
    share this rule (rules in core, nodes thin).
    """
    if not edited:
        return "", ""
    subject = str(edited.get("subject") or "")
    if "body" in edited and edited["body"] is not None:
        body = str(edited["body"])
        if not body.strip():
            raise ValueError(
                "edited draft body is blank — reject the draft instead of sending an empty message"
            )
        return subject, body
    return subject, ""


def plan_showing_followup_email(contact: Contact | None, listing_key: str) -> DraftContext | None:
    """The synced-feedback follow-up: email the toured contact, if we can.

    None when there is no contact or no email on file — the CRM sync already
    happened; the workflow just has no drafted tail to offer.
    """
    if contact is None or not contact.email:
        return None
    return DraftContext(
        recipient=contact.email,
        template_ref=SHOWING_FOLLOWUP_V1.ref,
        params={
            "recipient_name": contact.name or contact.email,
            "listing_address": listing_key,
            "sender_name": SENDER_NAME,
        },
        contact_id=contact.crm_id,
        listing_key=listing_key,
    )
