import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from brokerops_api.db import (
    InMemoryApprovalRepo,
    InMemoryFeedbackStore,
    InMemoryTransactionStore,
    SqlApprovalRepo,
    SqlFeedbackStore,
    SqlTransactionStore,
    create_engine,
)
from brokerops_api.deps import (
    build_crm_adapter,
    build_extraction_port,
    build_mls_adapter,
    build_voice_adapter,
)
from brokerops_api.routes.approvals import router as approvals_router
from brokerops_api.routes.calls import router as calls_router
from brokerops_api.routes.contacts import router as contacts_router
from brokerops_api.routes.cron import router as cron_router
from brokerops_api.routes.demo import router as demo_router
from brokerops_api.routes.listings import router as listings_router
from brokerops_api.routes.transactions import router as transactions_router
from brokerops_api.routes.webhooks import router as webhooks_router
from brokerops_api.routes.workflows import router as workflows_router
from brokerops_adk.engine import build_engine as build_adk_engine
from brokerops_langgraph.engine import build_engine as build_langgraph_engine

# Both engines honor the same WorkflowEngine protocol over the same MCP
# adapters, stores, and ApprovalRequest spine — the switch is wiring only.
ENGINE_FACTORIES = {
    "langgraph": build_langgraph_engine,
    "adk": build_adk_engine,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    orchestrator = os.environ.get("ORCHESTRATOR", "langgraph")
    if orchestrator not in ENGINE_FACTORIES:
        raise ValueError(
            f"unknown ORCHESTRATOR {orchestrator!r}; expected one of {sorted(ENGINE_FACTORIES)}"
        )
    build_engine = ENGINE_FACTORIES[orchestrator]
    database_url = os.environ.get("DATABASE_URL")
    mls = build_mls_adapter()
    crm = build_crm_adapter()
    voice = build_voice_adapter()
    extraction = build_extraction_port()
    app.state.crm = crm
    app.state.voice = voice
    engine = create_engine(database_url) if database_url else None
    if engine is not None:
        # Durable path: workflow state and approvals share Postgres, so HITL
        # pauses survive restarts and deploys.
        app.state.approval_repo = SqlApprovalRepo(engine)
        app.state.transaction_store = SqlTransactionStore(engine)
        app.state.feedback_store = SqlFeedbackStore(engine)
    else:
        # Database-less local dev: everything in memory, nothing survives.
        app.state.approval_repo = InMemoryApprovalRepo()
        app.state.transaction_store = InMemoryTransactionStore()
        app.state.feedback_store = InMemoryFeedbackStore()
    async with build_engine(
        mls=mls,
        crm=crm,
        voice=voice,
        extraction=extraction,
        transaction_store=app.state.transaction_store,
        feedback_store=app.state.feedback_store,
        approval_repo=app.state.approval_repo,
        database_url=database_url,
    ) as workflow_engine:
        app.state.workflow_engine = workflow_engine
        yield
    if engine is not None:
        await engine.dispose()


app = FastAPI(title="brokerops api", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(listings_router)
app.include_router(approvals_router)
app.include_router(contacts_router)
app.include_router(transactions_router)
app.include_router(workflows_router)
app.include_router(calls_router)
app.include_router(webhooks_router)
app.include_router(cron_router)
app.include_router(demo_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    # Container-internal liveness only: Google's frontend reserves /healthz
    # on *.run.app URLs and intercepts it — use /readyz for external checks.
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "brokerops api", "phase": "5"}
