from datetime import date

from pydantic import BaseModel


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
    email: str | None = None
    phone: str | None = None


class ContactCreate(BaseModel):
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    source: str = "brokerops"


class CrmTask(BaseModel):
    id: str
    name: str
    due_date: date | None = None
    contact_id: str | None = None
