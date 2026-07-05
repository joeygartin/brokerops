"""MCP server exposing the Sierra Interactive CRM tools.

Runs standalone over stdio (`uv run mcp-server-sierra`); points at any
Sierra-shaped endpoint via SIERRA_BASE_URL (real API by default, stub in demo
mode). Against the real API host, the API credential and the task assignee and
anchor settings must all be explicit — fail-loud, mirroring the api wiring
(ADR-0016): stub defaults must never silently write tasks onto whatever lead
their id happens to name in a real account. Pointing SIERRA_BASE_URL at a stub
keeps the stub's seeded defaults.
"""

import os
from datetime import date
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.contact import ContactCreate
from brokerops_sierra_crm.adapter import SIERRA_API_BASE, SierraCRMAdapter
from brokerops_sierra_crm.stub import STUB_TASK_ANCHOR_LEAD_ID, STUB_TASK_ASSIGNEE_ID

mcp = FastMCP("sierra")


def _require_env(name: str) -> str:
    # Same posture as the api wiring: "unset" is the Terraform placeholder for
    # a secret that was never pushed — the same misconfiguration as absent.
    value = os.environ.get(name, "")
    if not value or value == "unset":
        raise RuntimeError(f"{name} is required when SIERRA_BASE_URL points at the real Sierra API")
    return value


def _is_real_api(base_url: str) -> bool:
    # Normalize before the real-vs-stub compare (BOP-037): a trailing-slash or
    # case variant of the real host must take the real-API branch — never the
    # stub one, whose seeded assignee/anchor ids would silently write tasks
    # onto whatever lead they happen to name in a real account.
    return base_url.strip().rstrip("/").lower() == SIERRA_API_BASE


@lru_cache(maxsize=None)
def _cached_adapter(
    api_key: str, base_url: str, task_assignee_id: int, task_anchor_lead_id: str
) -> SierraCRMAdapter:
    # Built once per config and reused (BOP-037): a long-lived MCP server must
    # not leak one unclosed httpx.AsyncClient per tool call. Env is fixed for
    # the process lifetime, so this is one adapter in practice.
    return SierraCRMAdapter(
        api_key=api_key,
        base_url=base_url,
        task_assignee_id=task_assignee_id,
        task_anchor_lead_id=task_anchor_lead_id,
    )


def _adapter() -> SierraCRMAdapter:
    base_url = os.environ.get("SIERRA_BASE_URL", SIERRA_API_BASE)
    if _is_real_api(base_url):
        return _cached_adapter(
            _require_env("SIERRA_API_KEY"),
            SIERRA_API_BASE,
            int(_require_env("SIERRA_TASK_ASSIGNEE_ID")),
            _require_env("SIERRA_TASK_ANCHOR_LEAD_ID"),
        )
    # A non-default base URL is a stub/demo endpoint; the stub's seeded
    # assignee/anchor are safe defaults there and only there.
    return _cached_adapter(
        os.environ.get("SIERRA_API_KEY", ""),
        base_url,
        int(os.environ.get("SIERRA_TASK_ASSIGNEE_ID", str(STUB_TASK_ASSIGNEE_ID))),
        os.environ.get("SIERRA_TASK_ANCHOR_LEAD_ID", STUB_TASK_ANCHOR_LEAD_ID),
    )


async def get_contact(contact_id: str) -> dict[str, Any] | None:
    """Fetch a CRM contact by its Sierra Interactive lead id."""
    contact = await _adapter().get_contact(contact_id)
    return contact.model_dump(mode="json") if contact else None


async def search_contacts(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search CRM contacts by name or email."""
    contacts = await _adapter().search_contacts(query, limit)
    return [contact.model_dump(mode="json") for contact in contacts]


async def create_contact(
    first_name: str, last_name: str, email: str, phone: str | None = None
) -> dict[str, Any]:
    """Create a CRM contact. Sierra requires an email address."""
    draft = ContactCreate(first_name=first_name, last_name=last_name, email=email, phone=phone)
    contact = await _adapter().create_contact(draft)
    return contact.model_dump(mode="json")


async def add_note(contact_id: str, subject: str, body: str) -> str:
    """Attach a note to a CRM contact; returns the note id."""
    return await _adapter().add_note(contact_id, subject, body)


async def create_task(name: str, due_date: str, contact_id: str | None = None) -> dict[str, Any]:
    """Create a CRM task (due_date is ISO YYYY-MM-DD, required); returns the created task."""
    task = await _adapter().create_task(name, date.fromisoformat(due_date), contact_id)
    return task.model_dump(mode="json")


async def log_call(contact_id: str, outcome: str, note: str = "", duration_seconds: int = 0) -> str:
    """Log a call against a CRM contact (journaled as a Sierra note); returns its id."""
    return await _adapter().log_call(contact_id, outcome, note, duration_seconds)


mcp.tool()(get_contact)
mcp.tool()(search_contacts)
mcp.tool()(create_contact)
mcp.tool()(add_note)
mcp.tool()(create_task)
mcp.tool()(log_call)


def main() -> None:
    mcp.run()
