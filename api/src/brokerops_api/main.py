import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import InMemoryApprovalRepo, SqlApprovalRepo, create_engine
from brokerops_api.deps import reso_base_url
from brokerops_api.routes.approvals import router as approvals_router
from brokerops_api.routes.listings import router as listings_router
from brokerops_api.routes.workflows import router as workflows_router
from brokerops_api.workflows import WorkflowEngine
from brokerops_langgraph.checkpointer import postgres_checkpointer
from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract
from brokerops_mls_reso.adapter import ResoMLSAdapter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_url = os.environ.get("DATABASE_URL")
    mls = ResoMLSAdapter(base_url=reso_base_url())
    if database_url:
        # Durable path: graph state checkpoints and approvals share Postgres,
        # so HITL pauses survive restarts and deploys.
        engine = create_engine(database_url)
        async with postgres_checkpointer(database_url) as saver:
            app.state.approval_repo = SqlApprovalRepo(engine)
            graph = build_listing_to_contract(mls, saver)
            app.state.workflow_engine = WorkflowEngine(graph, app.state.approval_repo)
            yield
        await engine.dispose()
    else:
        # Database-less local dev: everything in memory, nothing survives.
        app.state.approval_repo = InMemoryApprovalRepo()
        graph = build_listing_to_contract(mls, InMemorySaver())
        app.state.workflow_engine = WorkflowEngine(graph, app.state.approval_repo)
        yield


app = FastAPI(title="brokerops api", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(listings_router)
app.include_router(approvals_router)
app.include_router(workflows_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "brokerops api", "phase": "2"}
