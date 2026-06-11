from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from brokerops_api.deps import get_workflow_engine
from brokerops_api.workflows import LISTING_TO_CONTRACT, WorkflowEngine, WorkflowRunResult

router = APIRouter(prefix="/workflows", tags=["workflows"])

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]


class StartListingToContract(BaseModel):
    listing_key: str


@router.post("/listing-to-contract/start", status_code=202)
async def start_listing_to_contract(
    body: StartListingToContract, engine: EngineDep
) -> WorkflowRunResult:
    return await engine.start(LISTING_TO_CONTRACT, {"listing_key": body.listing_key})
