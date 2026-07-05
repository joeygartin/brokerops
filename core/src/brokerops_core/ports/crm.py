from datetime import date
from typing import Protocol

from brokerops_core.models.contact import Contact, ContactCreate, CrmTask


class CRMPort(Protocol):
    """Boundary to the CRM (FollowUpBoss and Sierra Interactive in V1).

    CRM-specific payload shapes stay inside the integration package; services
    and workflows see only these methods and core models. The contract below
    is the intersection both vendors can honor (ADR-0016):

    - Ids (`Contact.crm_id`, note/task/call-log ids) live in the wired
      vendor's namespace and are opaque to callers.
    - Adapters raise ``ValueError`` for drafts that don't meet the vendor's
      documented minimum (e.g. Sierra requires an email to create a lead) and
      for write ids that cannot name a record in the vendor's namespace; reads
      given such an id return the port's "missing" value instead.
    """

    async def get_contact(self, contact_id: str) -> Contact | None: ...

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]: ...

    async def create_contact(self, draft: ContactCreate) -> Contact: ...

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        """Attach a note; returns its id. Vendors without a subject field fold
        the subject into the note body."""
        ...

    async def create_task(
        self, name: str, due_date: date, contact_id: str | None = None
    ) -> CrmTask:
        """Create a task due on `due_date` (required — Sierra cannot represent an
        undated task; ADR-0016). `contact_id=None` means "not tied to a contact";
        vendors whose task model is contact-anchored attach such tasks to a
        deploy-configured anchor contact, and the returned `CrmTask.contact_id`
        always echoes the caller's value."""
        ...

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        """Log a call against a contact; returns the id of the vendor record
        that journals it. Vendors expose different call-log surfaces: adapters
        map `outcome` onto the vendor's vocabulary, and a vendor with no
        call-log write endpoint journals the call as a note (ADR-0016)."""
        ...
