"""MCP server exposing the FollowUpBoss CRM tools.

Runs standalone over stdio (`uv run mcp-server-followupboss`); points at any
FUB-shaped endpoint via FUB_BASE_URL (real API by default, stub in demo mode).
"""

import os
from datetime import date
from typing import Any

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.contact import ContactCreate
from brokerops_followupboss.adapter import FUB_API_BASE, FUBCRMAdapter

mcp = FastMCP("followupboss")


def _adapter() -> FUBCRMAdapter:
    return FUBCRMAdapter(
        api_key=os.environ.get("FUB_API_KEY", ""),
        base_url=os.environ.get("FUB_BASE_URL", FUB_API_BASE),
    )


async def get_contact(contact_id: str) -> dict[str, Any] | None:
    """Fetch a CRM contact by its FollowUpBoss person id."""
    contact = await _adapter().get_contact(contact_id)
    return contact.model_dump(mode="json") if contact else None


async def search_contacts(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search CRM contacts by name or email."""
    contacts = await _adapter().search_contacts(query, limit)
    return [contact.model_dump(mode="json") for contact in contacts]


async def create_contact(
    first_name: str, last_name: str, email: str | None = None, phone: str | None = None
) -> dict[str, Any]:
    """Create a CRM contact. FollowUpBoss deduplicates by email on its side."""
    draft = ContactCreate(first_name=first_name, last_name=last_name, email=email, phone=phone)
    contact = await _adapter().create_contact(draft)
    return contact.model_dump(mode="json")


async def add_note(contact_id: str, subject: str, body: str) -> str:
    """Attach a note to a CRM contact; returns the note id."""
    return await _adapter().add_note(contact_id, subject, body)


async def create_task(
    name: str, due_date: str | None = None, contact_id: str | None = None
) -> dict[str, Any]:
    """Create a CRM task (due_date is ISO YYYY-MM-DD); returns the created task."""
    due = date.fromisoformat(due_date) if due_date else None
    task = await _adapter().create_task(name, due, contact_id)
    return task.model_dump(mode="json")


async def log_call(contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0) -> str:
    """Log a call against a CRM contact; returns the call id."""
    return await _adapter().log_call(contact_id, outcome, note, duration_seconds)


mcp.tool()(get_contact)
mcp.tool()(search_contacts)
mcp.tool()(create_contact)
mcp.tool()(add_note)
mcp.tool()(create_task)
mcp.tool()(log_call)


def main() -> None:
    mcp.run()
