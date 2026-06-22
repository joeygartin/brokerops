from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from brokerops_api.deps import get_workflow_engine, require_role
from brokerops_api.workflows import LISTING_TO_CONTRACT, WorkflowEngine, WorkflowRunResult
from brokerops_core.ports.identity import Principal, Role

router = APIRouter(prefix="/workflows", tags=["workflows"])

EngineDep = Annotated[WorkflowEngine, Depends(get_workflow_engine)]
# Starting a workflow is an action, not a read — operators and up, not viewers.
OperatorDep = Annotated[Principal, Depends(require_role(Role.OPERATOR))]


class StartListingToContract(BaseModel):
    listing_key: str


@router.post("/listing-to-contract/start", status_code=202)
async def start_listing_to_contract(
    body: StartListingToContract, engine: EngineDep, principal: OperatorDep
) -> WorkflowRunResult:
    return await engine.start(LISTING_TO_CONTRACT, {"listing_key": body.listing_key})
