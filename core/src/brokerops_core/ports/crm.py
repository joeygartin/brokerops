from datetime import date
from typing import Protocol

from brokerops_core.models.contact import Contact, ContactCreate, CrmTask


class CRMPort(Protocol):
    """Boundary to the CRM (FollowUpBoss in V1).

    CRM-specific payload shapes stay inside the integration package; services
    and workflows see only these methods and core models.
    """

    async def get_contact(self, contact_id: str) -> Contact | None: ...

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]: ...

    async def create_contact(self, draft: ContactCreate) -> Contact: ...

    async def add_note(self, contact_id: str, subject: str, body: str) -> str: ...

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask: ...

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str: ...
