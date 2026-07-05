import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from brokerops_api.db import (
    InMemoryApprovalRepo,
    InMemoryAuditLog,
    InMemoryDocumentStore,
    InMemoryFeedbackStore,
    InMemoryIdempotencyStore,
    InMemoryMagicTokenStore,
    InMemoryMessageStore,
    InMemoryTransactionStore,
    SqlApprovalRepo,
    SqlAuditLog,
    SqlDocumentStore,
    SqlFeedbackStore,
    SqlIdempotencyStore,
    SqlMagicTokenStore,
    SqlMessageStore,
    SqlTransactionStore,
    TenantScopedEngine,
    create_engine,
)
from brokerops_api.deps import (
    FILES_INTEGRATION,
    build_crm_adapter,
    build_email_port,
    build_extraction_port,
    build_files_adapter,
    build_identity_verifier,
    build_magic_link_service,
    build_mls_adapter,
    build_session_refresher,
    build_sms_port,
    build_voice_adapter,
    crm_vendor,
    demo_routes_enabled,
    deploy_tenant,
    get_current_principal,
)
from brokerops_api.tenant_middleware import TenantScopeMiddleware
from brokerops_api.routes.approvals import router as approvals_router
from brokerops_api.routes.audit import router as audit_router
from brokerops_api.routes.auth import router as auth_router
from brokerops_api.routes.calls import router as calls_router
from brokerops_api.routes.contacts import router as contacts_router
from brokerops_api.routes.cron import router as cron_router
from brokerops_api.routes.demo import router as demo_router
from brokerops_api.routes.documents import router as documents_router
from brokerops_api.routes.listings import router as listings_router
from brokerops_api.routes.messages import router as messages_router
from brokerops_api.routes.transactions import router as transactions_router
from brokerops_api.routes.webhooks import router as webhooks_router
from brokerops_api.routes.workflows import router as workflows_router
from brokerops_core.ports.auth import MagicTokenStore
from brokerops_core.services.audit import (
    RecordingCRM,
    RecordingEmail,
    RecordingFiles,
    RecordingSMS,
    RecordingVoice,
)
from brokerops_core.services.idempotency import (
    IdempotentCRM,
    IdempotentEmail,
    IdempotentFiles,
    IdempotentSMS,
    IdempotentVoice,
)
from brokerops_core.services.message_send import MessageSendService
from brokerops_core.services.scoped_stores import (
    ScopedApprovalRepo,
    ScopedDocumentStore,
    ScopedFeedbackStore,
    ScopedMessageStore,
    ScopedTransactionStore,
)
from brokerops_core.services.tool_authz import authorize_tool_ports
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
    files = build_files_adapter()
    extraction = build_extraction_port()
    # The outbound business-email provider (ADR-0015). An unknown or unwired
    # EMAIL_PROVIDER raises right here — misconfiguration is a startup error.
    email = build_email_port()
    # The outbound SMS provider (BOP-018), same posture over SMS_PROVIDER.
    sms = build_sms_port()
    app.state.crm = crm
    app.state.voice = voice
    app.state.email = email
    app.state.sms = sms
    engine = create_engine(database_url) if database_url else None
    magic_store: MagicTokenStore
    if engine is not None:
        # Durable path: workflow state, approvals, and the audit trail share
        # Postgres, so HITL pauses and the action ledger survive restarts/deploys.
        # Tenant-scoped stores run over a TenantScopedEngine that binds the tenant GUC
        # the row-level-security policy reads (BOP-006); the audit ledger is scoped
        # too so security events land in the right tenant. idempotency + magic tokens
        # keep the raw engine (internal infra / pre-auth — not under tenant RLS).
        scoped_engine = TenantScopedEngine(engine)
        app.state.audit_log = SqlAuditLog(scoped_engine)
        # BOP-011: authorize_tool_ports wraps each tenant-bearing store as the OUTERMOST
        # layer, so a tool call carrying a foreign tenant_id is rejected before the scoped
        # store (BOP-006) or the database (RLS) is reached. It gates entry only — the write
        # still flows through the same scoped/approval/idempotency/audit chain underneath.
        app.state.approval_repo = authorize_tool_ports(
            ScopedApprovalRepo(SqlApprovalRepo(scoped_engine), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.transaction_store = authorize_tool_ports(
            ScopedTransactionStore(SqlTransactionStore(scoped_engine), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.feedback_store = authorize_tool_ports(
            ScopedFeedbackStore(SqlFeedbackStore(scoped_engine), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.message_store = ScopedMessageStore(
            SqlMessageStore(scoped_engine), app.state.audit_log
        )
        app.state.document_store = ScopedDocumentStore(
            SqlDocumentStore(scoped_engine), app.state.audit_log
        )
        app.state.idempotency_store = SqlIdempotencyStore(engine)
        magic_store = SqlMagicTokenStore(engine)
    else:
        # Database-less local dev: everything in memory, nothing survives. The same
        # scoped wrappers enforce tenant confinement in-process, so demo and tests get
        # identical isolation without RLS.
        app.state.audit_log = InMemoryAuditLog()
        # Same BOP-011 authorization layer over the in-memory scoped stores, so demo/tests
        # get identical tool-input confinement without a database.
        app.state.approval_repo = authorize_tool_ports(
            ScopedApprovalRepo(InMemoryApprovalRepo(), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.transaction_store = authorize_tool_ports(
            ScopedTransactionStore(InMemoryTransactionStore(), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.feedback_store = authorize_tool_ports(
            ScopedFeedbackStore(InMemoryFeedbackStore(), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.message_store = ScopedMessageStore(InMemoryMessageStore(), app.state.audit_log)
        app.state.document_store = ScopedDocumentStore(InMemoryDocumentStore(), app.state.audit_log)
        app.state.idempotency_store = InMemoryIdempotencyStore()
        magic_store = InMemoryMagicTokenStore()
    # Magic-link service is None unless the "magic" method is enabled; routes
    # that need it 404 otherwise.
    app.state.magic_link_service = build_magic_link_service(magic_store)
    # Token refresh, present only when session tokens are issued (magic); /auth/refresh
    # 404s otherwise (ADR-0013).
    app.state.session_refresher = build_session_refresher()
    # The single write-boundary seam, two decorators deep: every external write the
    # engine performs is first deduped (IdempotentCRM/Voice — at most once per
    # workflow run, ADR-0011) and then recorded (RecordingCRM/Voice — the audit
    # ledger, ADR-0010). Idempotency wraps recording, so a deduped replay performs no
    # side effect and writes no second mutation record. Both engines inherit this
    # identically (architecture rule #5). The raw adapters stay on app.state for the
    # operator-driven direct routes, which are not workflow writes.
    #
    # BOP-011: tenant authorization is the OUTERMOST wrapper on every engine-reachable tool
    # port — for the write ports it runs BEFORE the idempotency claim and the audit record,
    # so a foreign tenant argument is rejected before ANY store/port side effect and never
    # lands as a full-payload failure entry on the mutation ledger (only the intended
    # security denial is recorded). The read-only MLS port is wrapped through the same seam
    # for uniform coverage; the stores are already authorized outermost (above).
    engine_mls = authorize_tool_ports(mls, audit=app.state.audit_log)
    engine_crm = authorize_tool_ports(
        IdempotentCRM(
            RecordingCRM(crm, app.state.audit_log, integration=crm_vendor()),
            app.state.idempotency_store,
        ),
        audit=app.state.audit_log,
    )
    engine_voice = authorize_tool_ports(
        IdempotentVoice(RecordingVoice(voice, app.state.audit_log), app.state.idempotency_store),
        audit=app.state.audit_log,
    )
    # Outbound email and SMS sends flow through the identical seam (ADR-0015 /
    # BOP-018): deduped, then recorded, over the tenant-scoped message store. The
    # /messages/send route binds the run context the decorators read via audit_scope.
    # Route-driven only today — when a workflow gains a send node (BOP-019), these
    # ports must be authorization-wrapped and registered in engine_tool_ports below.
    seam_email = IdempotentEmail(
        RecordingEmail(email, app.state.audit_log), app.state.idempotency_store
    )
    seam_sms = IdempotentSMS(RecordingSMS(sms, app.state.audit_log), app.state.idempotency_store)
    app.state.message_service = MessageSendService(
        email=seam_email, store=app.state.message_store, sms=seam_sms
    )
    # File writes go through the same two seams (BOP-021). Unlike crm/voice, the
    # wrapped port IS the app.state instance: the only file writer today is the
    # operator-driven documents route, and even that write must be recorded in
    # the ledger (reads pass through the wrappers untouched).
    app.state.files = IdempotentFiles(
        RecordingFiles(files, app.state.audit_log, integration=FILES_INTEGRATION),
        app.state.idempotency_store,
    )
    # The single enumerable registry of engine-reachable tool ports; the enumeration test
    # asserts every one is authorization-wrapped, so a new engine tool port cannot be added
    # unwrapped. (Raw crm/voice stay on app.state for the RBAC-gated operator routes, which
    # build their call context server-side and carry no tenant-bearing argument.)
    app.state.engine_tool_ports = {
        "mls": engine_mls,
        "crm": engine_crm,
        "voice": engine_voice,
        "transaction_store": app.state.transaction_store,
        "feedback_store": app.state.feedback_store,
        "approval_repo": app.state.approval_repo,
    }
    async with build_engine(
        mls=engine_mls,
        crm=engine_crm,
        voice=engine_voice,
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

# The identity verifier is resolved from env at import time (env is fixed for
# the process lifetime). Done at module scope, not in lifespan, so it's present
# even for tests that drive the app without running the lifespan. The magic-link
# service defaults to None here (no store yet); lifespan builds the real one.
app.state.identity_verifier = build_identity_verifier()
app.state.magic_link_service = None
app.state.session_refresher = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Bind this deploy's tenant around every request, below the agent (BOP-006). Added
# after CORS so it runs inside it; the data layer reads the tenant from this scope.
app.add_middleware(TenantScopeMiddleware, tenant_provider=deploy_tenant)

# Operator-facing routes require an authenticated principal. In demo mode the
# demo verifier resolves one with no token, so these stay open without a login;
# with an OIDC client configured they demand a valid Google bearer (ADR-0007).
operator_auth = [Depends(get_current_principal)]
app.include_router(listings_router, dependencies=operator_auth)
app.include_router(approvals_router, dependencies=operator_auth)
app.include_router(audit_router, dependencies=operator_auth)
app.include_router(contacts_router, dependencies=operator_auth)
app.include_router(transactions_router, dependencies=operator_auth)
app.include_router(documents_router, dependencies=operator_auth)
app.include_router(workflows_router, dependencies=operator_auth)
app.include_router(calls_router, dependencies=operator_auth)
app.include_router(messages_router, dependencies=operator_auth)

# Machine + public surfaces keep their own controls: webhooks verify their
# provider signature, cron its X-Cron-Key, and /auth bootstraps the SPA.
app.include_router(webhooks_router)
app.include_router(cron_router)
# The demo seed/reset surface can wipe tenant data, so it is mounted ONLY when
# ENABLE_DEMO_ROUTES is set (the demo opts in; clients never do). Gating at mount time —
# not per-request — means a client deploy has no /demo/* route at all: it's absent from
# the OpenAPI schema and any method returns 404, leaking no hint the surface exists.
# Env is fixed for the process lifetime, so reading it here (like the identity verifier
# above) is correct.
if demo_routes_enabled():
    app.include_router(demo_router)
app.include_router(auth_router)


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
