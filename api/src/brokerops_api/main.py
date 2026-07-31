import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from brokerops_api.db import (
    InMemoryApprovalRepo,
    InMemoryAuditLog,
    InMemoryCronRunStore,
    InMemoryDocumentStore,
    InMemoryFeedbackStore,
    InMemoryIdempotencyStore,
    InMemoryMagicTokenStore,
    InMemoryMessageStore,
    InMemoryTransactionStore,
    SqlApprovalRepo,
    SqlAuditLog,
    SqlCronRunStore,
    SqlDocumentStore,
    SqlFeedbackStore,
    SqlIdempotencyStore,
    SqlMagicTokenStore,
    SqlMessageStore,
    SqlTransactionStore,
    TenantScopedEngine,
    create_engine,
)
from brokerops_api.logging_config import configure_logging
from brokerops_api.status import build_status_payload
from brokerops_api.deps import (
    FILES_INTEGRATION,
    build_crm_adapter,
    build_drafting_port,
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
    get_cron_run_store,
    get_current_principal,
    get_migration_engine,
    get_started_at,
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
from brokerops_core.services.egress import guard_tool_ports
from brokerops_langgraph.engine import build_engine as build_langgraph_engine

# Install JSON log format on Cloud Run (or LOG_FORMAT=json) before uvicorn starts
# emitting access lines — BOP-035 structured-logging hook.
configure_logging()

# LangGraph is the sole orchestrator (ADR-0019). The API depends only on the
# WorkflowEngine protocol; the engine lives behind it in its own package, so the
# seam that once made a second-engine port mechanical is retained. The selector stays
# fail-loud and single-valued — a stray ORCHESTRATOR raises at startup rather than
# silently doing anything (the EXTRACTION_BACKEND / EMAIL_PROVIDER posture).
ENGINE_FACTORIES = {
    "langgraph": build_langgraph_engine,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    orchestrator = os.environ.get("ORCHESTRATOR", "langgraph")
    if orchestrator not in ENGINE_FACTORIES:
        raise ValueError(
            f"unknown ORCHESTRATOR {orchestrator!r}; expected one of {sorted(ENGINE_FACTORIES)}"
        )
    build_engine = ENGINE_FACTORIES[orchestrator]
    # DATABASE_URL is the runtime DSN the tenant-scoped domain stores connect
    # with; in a hardened deploy it is a non-owner, non-BYPASSRLS role so the
    # forced RLS policy binds (BOP-013 / ADR-0021). Schema-managing code — the
    # LangGraph checkpointer's setup() (and alembic, via its own env) — needs the
    # owner role instead, read from MIGRATION_DATABASE_URL. Local/compose leaves
    # MIGRATION_DATABASE_URL unset, so both collapse to one role, unchanged.
    database_url = os.environ.get("DATABASE_URL")
    schema_database_url = os.environ.get("MIGRATION_DATABASE_URL") or database_url
    # Uptime clock for /statusz (monotonic — immune to wall-clock steps).
    app.state.started_at = time.monotonic()
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
    # Owner/migration engine for schema probes (/statusz alembic head-vs-current).
    # When roles are split, the runtime DSN cannot SELECT alembic_version (0010).
    if schema_database_url and schema_database_url != database_url:
        app.state.migration_engine = create_engine(schema_database_url)
    else:
        app.state.migration_engine = engine
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
        # BOP-011 + BOP-012: guard_tool_ports wraps each tenant-bearing store with BOTH
        # directions of the uniform tool seam as the OUTERMOST layer — a tool call carrying
        # a foreign tenant_id is rejected before the scoped store (BOP-006) or the database
        # (RLS) is reached, and every response is egress-filtered (cross-tenant identifiers
        # block; secret shapes and role-restricted PII redact) before it returns. It gates
        # the boundary only — the write still flows through the same
        # scoped/approval/idempotency/audit chain underneath.
        app.state.approval_repo = guard_tool_ports(
            ScopedApprovalRepo(SqlApprovalRepo(scoped_engine), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.transaction_store = guard_tool_ports(
            ScopedTransactionStore(SqlTransactionStore(scoped_engine), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.feedback_store = guard_tool_ports(
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
        # Cron outcome store is instance-level (raw engine), not tenant-scoped — BOP-035.
        app.state.cron_run_store = SqlCronRunStore(engine)
        magic_store = SqlMagicTokenStore(engine)
    else:
        # Database-less local dev: everything in memory, nothing survives. The same
        # scoped wrappers enforce tenant confinement in-process, so demo and tests get
        # identical isolation without RLS.
        app.state.audit_log = InMemoryAuditLog()
        # Same BOP-011/BOP-012 seam over the in-memory scoped stores, so demo/tests get
        # identical tool-input confinement and egress filtering without a database.
        app.state.approval_repo = guard_tool_ports(
            ScopedApprovalRepo(InMemoryApprovalRepo(), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.transaction_store = guard_tool_ports(
            ScopedTransactionStore(InMemoryTransactionStore(), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.feedback_store = guard_tool_ports(
            ScopedFeedbackStore(InMemoryFeedbackStore(), app.state.audit_log),
            audit=app.state.audit_log,
        )
        app.state.message_store = ScopedMessageStore(InMemoryMessageStore(), app.state.audit_log)
        app.state.document_store = ScopedDocumentStore(InMemoryDocumentStore(), app.state.audit_log)
        app.state.idempotency_store = InMemoryIdempotencyStore()
        app.state.cron_run_store = InMemoryCronRunStore()
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
    # side effect and writes no second mutation record. This sits below the engine,
    # so it holds with no engine-specific code (architecture rule #5). The raw
    # adapters stay on app.state for the
    # operator-driven direct routes, which are not workflow writes.
    #
    # BOP-011 + BOP-012: the both-directions tool guard is the OUTERMOST wrapper on every
    # engine-reachable tool port. Inbound, a foreign tenant argument is rejected BEFORE the
    # idempotency claim and the audit record — before ANY store/port side effect — and never
    # lands as a full-payload failure entry on the mutation ledger (only the intended
    # security denial is recorded). Outbound, every response is egress-filtered before the
    # engine sees it: a foreign tenant identifier blocks the whole response fail-closed, and
    # secret shapes / role-restricted PII redact in place. The engine seam's recipient is
    # the agent, wired at the OPERATOR tier (it acts — drafts comms, places calls — but
    # never decides, per ADR-0009), so contact-reach PII the workflows legitimately consume
    # stays visible while everything above that bar is filtered even from the agent. The
    # read-only MLS port goes through the same seam for uniform coverage; the stores are
    # already guarded outermost (above).
    engine_mls = guard_tool_ports(mls, audit=app.state.audit_log)
    engine_crm = guard_tool_ports(
        IdempotentCRM(
            RecordingCRM(crm, app.state.audit_log, integration=crm_vendor()),
            app.state.idempotency_store,
        ),
        audit=app.state.audit_log,
    )
    engine_voice = guard_tool_ports(
        IdempotentVoice(RecordingVoice(voice, app.state.audit_log), app.state.idempotency_store),
        audit=app.state.audit_log,
    )
    # Outbound email and SMS sends flow through the identical seam (ADR-0015 /
    # BOP-018): deduped, then recorded, over the tenant-scoped message store. The
    # /messages/send route binds the run context the decorators read via audit_scope.
    # BOP-019 made the email seam ENGINE-REACHABLE (workflow send-on-approve nodes),
    # so it is guarded outermost like every other engine tool port and registered in
    # engine_tool_ports below; its `send(message)` is tenant-bearing, and the egress
    # half of the guard (BOP-012) is the filter BOP-020's LLM-drafting wiring gate
    # checks for on this seam (EGRESS_FILTERED_MARKER). The SMS seam stays
    # route-driven only (no workflow drafts SMS in v1) — when a workflow gains an
    # SMS send node, wrap and register it the same way.
    seam_email = guard_tool_ports(
        IdempotentEmail(RecordingEmail(email, app.state.audit_log), app.state.idempotency_store),
        audit=app.state.audit_log,
    )
    # The SMS seam is guarded identically to email: MessageSendService can send an
    # approved draft through *either* channel (send_approved dispatches by the row's
    # channel), so both are drafted-egress paths and both must carry the BOP-012
    # guard treatment. (No workflow drafts SMS in v1, but that is enforced by the
    # gate below, not trusted as a convention.)
    seam_sms = guard_tool_ports(
        IdempotentSMS(RecordingSMS(sms, app.state.audit_log), app.state.idempotency_store),
        audit=app.state.audit_log,
    )
    # The drafting backend (BOP-019/020) hangs off the same service: workflow-drafted
    # comms are persisted PENDING_APPROVAL and only a human decision sends them
    # through the seams above — audited, deduped, tenant-scoped, authorized for free.
    # LLM-drafted output (DRAFTING_BACKEND=pydantic_ai, BOP-020) crosses to an external
    # party only through the DLP seam, enforced two ways: (1) MessageSendService
    # secret-shape-scrubs every outbound payload before port.send — the real
    # outbound-direction complement of BOP-012's result filter; and (2) this wiring
    # hard-gates the LLM backend so build_drafting_port refuses it unless EVERY channel
    # seam a draft can egress through carries the BOP-012 guard treatment (defense in
    # depth — the LLM never wires onto an unhardened seam). Both drafted-egress seams
    # (email + SMS) are passed, so the gate covers every channel send_approved can reach.
    app.state.message_service = MessageSendService(
        email=seam_email,
        store=app.state.message_store,
        sms=seam_sms,
        drafting=build_drafting_port(seam_email, seam_sms),
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
    # asserts every one is guarded in BOTH directions (authorization-wrapped AND
    # egress-filtered), so a new engine tool port cannot be added unwrapped in either.
    # (Raw crm/voice stay on app.state for the RBAC-gated operator routes, which build
    # their call context server-side and carry no tenant-bearing argument.)
    app.state.engine_tool_ports = {
        "mls": engine_mls,
        "crm": engine_crm,
        "voice": engine_voice,
        "transaction_store": app.state.transaction_store,
        "feedback_store": app.state.feedback_store,
        "approval_repo": app.state.approval_repo,
        # BOP-019/020: the workflows' send-on-approve nodes reach the email and SMS
        # seams (send_approved dispatches by the drafted row's channel), so both are
        # engine-reachable send paths and both are guarded + registered.
        "email": seam_email,
        "sms": seam_sms,
    }
    async with build_engine(
        mls=engine_mls,
        crm=engine_crm,
        voice=engine_voice,
        extraction=extraction,
        transaction_store=app.state.transaction_store,
        feedback_store=app.state.feedback_store,
        approval_repo=app.state.approval_repo,
        message_service=app.state.message_service,
        database_url=schema_database_url,
    ) as workflow_engine:
        app.state.workflow_engine = workflow_engine
        yield
    migration_engine = getattr(app.state, "migration_engine", None)
    if migration_engine is not None and migration_engine is not engine:
        await migration_engine.dispose()
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
# Defaults so tests that skip the lifespan still resolve /statusz deps.
app.state.started_at = time.monotonic()
app.state.cron_run_store = InMemoryCronRunStore()
app.state.migration_engine = None

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
    # Unauthenticated minimal readiness for load balancers + fleet-upgrade
    # verify (BOP-033). Detail lives on /statusz (authenticated or internal key).
    return {"status": "ok"}


async def _require_status_access(
    authorization: Annotated[str | None, Header()] = None,
    x_status_key: Annotated[str | None, Header()] = None,
) -> None:
    """Admit /statusz via viewer+ principal OR a matching internal status key.

    Fail closed: no valid key and no authenticated principal → 401. A wrong key
    does not short-circuit a valid bearer — operator tokens still work.
    """
    secret = os.environ.get("STATUS_INTERNAL_KEY", "")
    if secret and x_status_key == secret:
        return
    # Reuse the same principal resolution as operator routes (demo verifier
    # admits without a token; OIDC/magic require a real bearer). Detail requires
    # viewer+ (task fail-closed posture) — never admit a verified-but-sub-viewer
    # identity even if one is ever constructed.
    verifier = app.state.identity_verifier
    token = authorization
    if token and token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        from brokerops_core.models.role import Role
        from brokerops_core.ports.identity import AuthError

        principal = await verifier.verify(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=403 if exc.forbidden else 401,
            detail=str(exc),
        ) from exc
    if not principal.role.allows(Role.VIEWER):
        raise HTTPException(
            status_code=403,
            detail=f"requires {Role.VIEWER.value} role (you are {principal.role.value})",
        )


@app.get("/statusz")
async def statusz(
    _: Annotated[None, Depends(_require_status_access)],
    cron_store: Annotated[Any, Depends(get_cron_run_store)],
    migration_engine: Annotated[Any, Depends(get_migration_engine)],
    started_at: Annotated[float, Depends(get_started_at)],
) -> dict[str, Any]:
    """Per-instance health detail (BOP-035). Viewer role or X-Status-Key."""
    return await build_status_payload(
        started_at=started_at,
        cron_store=cron_store,
        migration_engine=migration_engine,
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "brokerops api", "phase": "5"}
