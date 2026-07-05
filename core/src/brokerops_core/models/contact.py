from datetime import date
from typing import Annotated

from pydantic import BaseModel

from brokerops_core.models.sensitivity import CONTACT_PII


class Contact(BaseModel):
    """CRM contact DTO. The CRM behind `CRMPort` is the source of truth —
    this is a read-through shape, never persisted on our side.

    `crm_id` is the contact's id in whichever CRM this deploy is wired to
    (a FollowUpBoss person id, a Sierra Interactive lead id, …); it is only
    meaningful to the adapter that produced it.
    """

    crm_id: str
    name: str
    role: str = "Lead"
    # Direct reach is role-restricted PII at the egress boundary (BOP-012).
    email: Annotated[str | None, CONTACT_PII] = None
    phone: Annotated[str | None, CONTACT_PII] = None


class ContactCreate(BaseModel):
    first_name: str
    last_name: str
    email: Annotated[str | None, CONTACT_PII] = None
    phone: Annotated[str | None, CONTACT_PII] = None
    source: str = "brokerops"


class CrmTask(BaseModel):
    id: str
    name: str
    due_date: date | None = None
    contact_id: str | None = None
