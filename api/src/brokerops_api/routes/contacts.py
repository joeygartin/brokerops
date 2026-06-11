from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from brokerops_api.deps import get_crm_port
from brokerops_core.models.contact import Contact
from brokerops_core.ports.crm import CRMPort

router = APIRouter(prefix="/contacts", tags=["contacts"])

CRMDep = Annotated[CRMPort, Depends(get_crm_port)]


@router.get("")
async def search_contacts(crm: CRMDep, q: str = "", limit: int = 20) -> list[Contact]:
    return await crm.search_contacts(q, limit)


@router.get("/{contact_id}")
async def get_contact(contact_id: str, crm: CRMDep) -> Contact:
    contact = await crm.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail=f"contact {contact_id!r} not found")
    return contact
