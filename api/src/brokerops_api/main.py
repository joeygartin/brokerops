import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from brokerops_api.db import (
    InMemoryApprovalRepo,
    InMemoryFeedbackStore,
    InMemoryTransactionStore,
    SqlApprovalRepo,
    SqlFeedbackStore,
    SqlTransactionStore,
    create_engine,
)
from brokerops_api.deps import build_crm_adapter, build_voice_adapter, reso_base_url
from brokerops_api.routes.approvals import router as approvals_router
from brokerops_api.routes.calls import router as calls_router
from brokerops_api.routes.contacts import router as contacts_router
from brokerops_api.routes.cron import router as cron_router
from brokerops_api.routes.demo import router as demo_router
from brokerops_api.routes.listings import router as listings_router
from brokerops_api.routes.transactions import router as transactions_router
from brokerops_api.routes.webhooks import router as webhooks_router
from brokerops_api.routes.workflows import router as workflows_router
from brokerops_api.workflows import (
    LISTING_TO_CONTRACT,
    TRANSACTION_COORDINATION,
    VAPI_FOLLOWUP,
    WorkflowEngine,
)
from brokerops_langgraph.checkpointer import postgres_checkpointer
from brokerops_langgraph.graphs.listing_to_contract import build_listing_to_contract
from brokerops_langgraph.graphs.transaction_coordination import build_transaction_coordination
from brokerops_langgraph.graphs.vapi_followup import build_vapi_followup
from brokerops_mls_reso.adapter import ResoMLSAdapter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database_url = os.environ.get("DATABASE_URL")
    mls = ResoMLSAdapter(base_url=reso_base_url())
    crm = build_crm_adapter()
    voice = build_voice_adapter()
    app.state.crm = crm
    app.state.voice = voice
    if database_url:
        # Durable path: graph state checkpoints and approvals share Postgres,
        # so HITL pauses survive restarts and deploys.
        engine = create_engine(database_url)
        async with postgres_checkpointer(database_url) as saver:
            app.state.approval_repo = SqlApprovalRepo(engine)
            app.state.transaction_store = SqlTransactionStore(engine)
            app.state.feedback_store = SqlFeedbackStore(engine)
            graphs = {
                LISTING_TO_CONTRACT: build_listing_to_contract(mls, crm, saver),
                TRANSACTION_COORDINATION: build_transaction_coordination(
                    app.state.transaction_store, crm, saver
                ),
                VAPI_FOLLOWUP: build_vapi_followup(voice, crm, app.state.feedback_store, saver),
            }
            app.state.workflow_engine = WorkflowEngine(graphs, app.state.approval_repo)
            yield
        await engine.dispose()
    else:
        # Database-less local dev: everything in memory, nothing survives.
        app.state.approval_repo = InMemoryApprovalRepo()
        app.state.transaction_store = InMemoryTransactionStore()
        app.state.feedback_store = InMemoryFeedbackStore()
        memory_saver = InMemorySaver()
        graphs = {
            LISTING_TO_CONTRACT: build_listing_to_contract(mls, crm, memory_saver),
            TRANSACTION_COORDINATION: build_transaction_coordination(
                app.state.transaction_store, crm, memory_saver
            ),
            VAPI_FOLLOWUP: build_vapi_followup(voice, crm, app.state.feedback_store, memory_saver),
        }
        app.state.workflow_engine = WorkflowEngine(graphs, app.state.approval_repo)
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
app.include_router(contacts_router)
app.include_router(transactions_router)
app.include_router(workflows_router)
app.include_router(calls_router)
app.include_router(webhooks_router)
app.include_router(cron_router)
app.include_router(demo_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "brokerops api", "phase": "5"}
