"""CRMPort adapter speaking the FollowUpBoss REST API.

Auth is HTTP basic with the API key as username (FUB convention). The same
adapter runs against the bundled stub (demo mode) and the real API — swapping
is a base-URL + key change. FUB payload shapes never leave this module.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

from brokerops_core.models.contact import Contact, ContactCreate, CrmTask
from brokerops_followupboss.ratelimit import TokenBucket

FUB_API_BASE = "https://api.followupboss.com/v1"

# FUB's /calls endpoint accepts a fixed outcome vocabulary. The feedback
# workflow hands us a sentiment ("positive"/"negative"/"neutral"); every such
# call connected and feedback was collected, so they all log as "Interested" —
# the sentiment itself lives in the accompanying note, not the call disposition
# (Joey's call, 2026-06-17). Already-valid FUB values pass through unchanged.
# Keys are lowercased; an unmapped value means we omit outcome (it is optional).
_FUB_CALL_OUTCOMES = {
    "positive": "Interested",
    "neutral": "Interested",
    "negative": "Interested",
    "interested": "Interested",
    "not interested": "Not Interested",
    "left message": "Left Message",
    "no answer": "No Answer",
    "busy": "Busy",
    "bad number": "Bad Number",
}


def contact_from_fub(person: Mapping[str, Any]) -> Contact:
    emails = person.get("emails") or []
    phones = person.get("phones") or []
    first = person.get("firstName") or ""
    last = person.get("lastName") or ""
    return Contact(
        fub_id=str(person["id"]),
        name=person.get("name") or f"{first} {last}".strip(),
        role=person.get("stage") or "Lead",
        email=emails[0]["value"] if emails else None,
        phone=phones[0]["value"] if phones else None,
    )


class FUBCRMAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str = FUB_API_BASE,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, auth=(api_key, ""), timeout=15.0
        )
        self._bucket = bucket or TokenBucket(capacity=10, refill_per_second=2.0)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        await self._bucket.acquire()
        return await self._client.request(method, path, **kwargs)

    async def get_contact(self, contact_id: str) -> Contact | None:
        response = await self._request("GET", f"/people/{contact_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return contact_from_fub(response.json())

    async def search_contacts(self, query: str, limit: int = 20) -> list[Contact]:
        response = await self._request("GET", "/people", params={"q": query, "limit": str(limit)})
        response.raise_for_status()
        return [contact_from_fub(person) for person in response.json()["people"]]

    async def create_contact(self, draft: ContactCreate) -> Contact:
        payload: dict[str, Any] = {
            "firstName": draft.first_name,
            "lastName": draft.last_name,
            "source": draft.source,
        }
        if draft.email:
            payload["emails"] = [{"value": draft.email}]
        if draft.phone:
            payload["phones"] = [{"value": draft.phone}]
        response = await self._request("POST", "/people", json=payload)
        response.raise_for_status()
        return contact_from_fub(response.json())

    async def add_note(self, contact_id: str, subject: str, body: str) -> str:
        response = await self._request(
            "POST", "/notes", json={"personId": int(contact_id), "subject": subject, "body": body}
        )
        response.raise_for_status()
        return str(response.json()["id"])

    async def create_task(
        self, name: str, due_date: date | None = None, contact_id: str | None = None
    ) -> CrmTask:
        payload: dict[str, Any] = {"name": name}
        if due_date is not None:
            payload["dueDate"] = due_date.isoformat()
        if contact_id is not None:
            payload["personId"] = int(contact_id)
        response = await self._request("POST", "/tasks", json=payload)
        response.raise_for_status()
        body = response.json()
        return CrmTask(
            id=str(body["id"]),
            name=body.get("name", name),
            due_date=due_date,
            contact_id=contact_id,
        )

    async def log_call(
        self, contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0
    ) -> str:
        # Real FUB requires the contact's phone and a direction flag, and only
        # accepts a fixed outcome vocabulary — concerns that stay inside this
        # adapter so the port and workflow nodes never learn FUB's shape.
        # brokerops only ever logs the outbound feedback calls it placed.
        contact = await self.get_contact(contact_id)
        payload: dict[str, Any] = {
            "personId": int(contact_id),
            "isIncoming": False,
            "note": note,
            "duration": duration_seconds,
        }
        if contact is not None and contact.phone:
            payload["phone"] = contact.phone
        mapped_outcome = _FUB_CALL_OUTCOMES.get(outcome.strip().lower())
        if mapped_outcome is not None:
            payload["outcome"] = mapped_outcome
        response = await self._request("POST", "/calls", json=payload)
        response.raise_for_status()
        return str(response.json()["id"])
