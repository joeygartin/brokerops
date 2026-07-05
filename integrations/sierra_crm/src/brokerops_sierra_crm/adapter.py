"""CRMPort adapter speaking the Sierra Interactive REST API.

Auth is the ``Sierra-ApiKey`` header (Sierra convention). Every Sierra response
is a ``{"success": bool, "data": ...}`` envelope with ``errorMessage`` on
failure; that envelope, and every other Sierra payload shape, never leaves this
module. The same adapter runs against the bundled stub (demo mode) and the real
API — swapping is a base-URL + key change.

Vendor constraints the port absorbs (ADR-0015):

- Sierra tasks are always lead-attached and always assigned: a port-level task
  with no ``contact_id`` is anchored to a deploy-configured lead
  (``task_anchor_lead_id``), and every task is assigned to a deploy-configured
  admin user (``task_assignee_id``).
- Sierra's public API has no call-log write endpoint (``/phoneCall/{id}`` is
  read-only), so ``log_call`` journals the call as a lead note.
- Creating a lead requires an email address and a password; the adapter rejects
  email-less drafts and generates a throwaway password (the lead-site login is
  not brokerops's concern), with the registration email suppressed.
- Sierra documents no API rate limits, so there is no client-side token bucket
  here (unlike FollowUpBoss, whose limits are documented and enforced).
"""

import secrets
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

from brokerops_core.models.contact import Contact, ContactCreate, CrmTask

SIERRA_API_BASE = "https://api.sierrainteractivedev.com"


def contact_from_sierra(lead: Mapping[str, Any]) -> Contact:
    first = lead.get("firstName") or ""
    last = lead.get("lastName") or ""
    return Contact(
        crm_id=str(lead["id"]),
        name=f"{first} {last}".strip(),
        role=lead.get("leadStatus") or "Lead",
        email=lead.get("email") or None,
        phone=lead.get("phone") or None,
    )


class SierraCRMAdapter:
    def __init__(
        self,
        api_key: str,
        *,
        task_assignee_id: int,
        task_anchor_lead_id: str,
        base_url: str = SIERRA_API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, headers={"Sierra-ApiKey": api_key}, timeout=15.0
        )
        self._task_assignee_id = task_assignee_id
        self._task_anchor_lead_id = task_anchor_lead_id

    @staticmethod
    def _data(response: httpx.Response) -> Any:
        response.raise_for_status()
        return response.json()["data"]

    async def get_contact(self, contact_id: str) -> Contact | None:
        response = await self._client.get(f"/leads/get/{contact_id}")
        if response.status_code == 404:
            return None
        return contact_from_sierra(self._data(response))

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        # Sierra's find is field-based, not free-text: an @ means the caller is
        # searching by email (full or partial), anything else searches by name.
        field = "email" if "@" in query else "name"
        params = {field: query, "pageSize": str(min(max(limit, 1), 100))}
        response = await self._client.get("/leads/find", params=params)
        return [contact_from_sierra(lead) for lead in self._data(response)["leads"]]

    async def create_contact(self, draft: ContactCreate) -> Contact:
        if not draft.email:
            raise ValueError("Sierra Interactive requires an email address to create a lead")
        payload: dict[str, Any] = {
            "firstName": draft.first_name,
            "lastName": draft.last_name,
            "email": draft.email,
            # Required by Sierra for the lead's site account; brokerops never
            # uses it, and the registration email is suppressed.
            "password": secrets.token_urlsafe(16),
            "sendRegistrationEmail": False,
            "source": draft.source,
        }
        if draft.phone:
            payload["phone"] = draft.phone
        response = await self._client.post("/leads", json=payload)
        # Registration returns assignment details only, not the lead record —
        # read the created lead back so callers get the CRM's truth.
        lead_id = str(self._data(response)["leadId"])
        created = await self.get_contact(lead_id)
        if created is None:  # defensive: the lead we just created must exist
            raise RuntimeError(f"Sierra lead {lead_id} vanished after creation")
        return created

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        # Sierra notes have no subject field; fold it into the message.
        message = f"{subject}\n\n{body}" if subject else body
        response = await self._client.post(f"/leads/{contact_id}/note", json={"message": message})
        return str(self._data(response)["id"])

    async def create_task(
        self, name: str, due_date: date, contact_id: str | None = None
    ) -> CrmTask:
        lead_id = contact_id if contact_id is not None else self._task_anchor_lead_id
        payload = {
            "toUserId": self._task_assignee_id,
            "dueOn": f"{due_date.isoformat()}T00:00:00Z",
            "taskType": "Other",
            "contents": name,
        }
        response = await self._client.post(f"/leads/{lead_id}/task", json=payload)
        body = self._data(response)
        # contact_id echoes the caller's value: the anchor lead is deploy
        # plumbing, not task semantics (ADR-0015).
        return CrmTask(id=str(body["id"]), name=name, due_date=due_date, contact_id=contact_id)

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        # Sierra's public API reads phone calls but cannot write them, so the
        # call is journaled as a note on the lead; the returned id is a note id.
        message = f"Call log — outcome: {outcome}; duration: {duration_seconds}s"
        if note:
            message = f"{message}\n\n{note}"
        response = await self._client.post(f"/leads/{contact_id}/note", json={"message": message})
        return str(self._data(response)["id"])
