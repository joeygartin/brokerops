from datetime import date

from pydantic import BaseModel


class Contact(BaseModel):
    """CRM contact DTO. The CRM (FollowUpBoss) is the source of truth —
    this is a read-through shape, never persisted on our side."""

    fub_id: str
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
