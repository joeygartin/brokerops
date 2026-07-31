# brokerops — Architecture

AI-powered backoffice for real estate brokerages: listing-to-contract marketing,
deadline-driven transaction coordination, and voice follow-up — every consequential
action gated by human approval.

A production agent architecture: three workflows run on LangGraph behind a thin
`WorkflowEngine` seam that keeps orchestration out of the domain core, with durable
human-in-the-loop, an MCP tool boundary, hexagonal domain isolation, operator auth
with role-based access, and per-client GCP deploys via Terraform.

## Principles

1. **Hexagonal core.** All domain logic lives in `core/` as plain Python + Pydantic.
   `core/` imports no LangGraph, no FastAPI, no SDK. The orchestration framework is a
   shell around it, behind the `WorkflowEngine` seam.
2. **MCP as the tool boundary.** Every external system (MLS, FollowUpBoss, Vapi) is
   an MCP server, independently runnable over stdio. The integration layer is
   written once; any MCP-native orchestrator can consume it.
3. **Thin nodes.** A workflow node may read state, call a core service or port, and
   write state. Business rules live in `core/services/` — if a node grows an `if`
   tree about real estate, that logic moves to core.
4. **RESO-real mock.** The bundled MLS implements a genuine subset of the RESO Web
   API (OData `$filter` with eq/gt/lt/and/or, `$select`, `$top`, `$skip`,
   `$orderby`; RESO Data Dictionary field names). Swapping to a live MLS feed is a
   base-URL + bearer-token change, pinned by contract tests — proven against a
   real vendor's RESO Web API, which also taught the domain model that sparse
   fields (no rooms, no price, no address) are data, not errors.
5. **State is durable.** Workflow state lives in Postgres (LangGraph's checkpointer).
   A workflow paused on a human approval survives restarts, deploys, and Cloud Run
   cold starts — proven by an automated restart test and a live container-restart
   drill.
6. **Public-repo paranoia.** Secrets never touch the repo: gitleaks pre-commit and
   CI, GitHub push protection, Secret Manager for runtime, `.env.example` with fake
   values. Seed data is fully synthetic.
7. **Demo mode is a first-class target.** `docker compose up` runs the whole stack
   with zero credentials; the demo path is asserted by a scripted e2e check.

## System shape

```
                      ┌─────────────────── GCP project (per client) ───────────────────┐
                      │                                                                │
 Browser ────────────▶│  Cloud Run: frontend (static bundle, nginx)                    │
                      │      │  /api/* proxied same-origin (ADR-0003)                  │
                      │      ▼                                                         │
 Vapi webhooks ──────▶│  Cloud Run: api (FastAPI)                                      │
 Cloud Scheduler ────▶│      ├── orchestration/langgraph  (3 workflows behind the     │
                      │      │        │      WorkflowEngine seam — ADR-0019)           │
                      │      │        │ ports (Protocol)                               │
                      │      │        ├── integrations/mls_reso      (RESO Web API)    │
                      │      │        ├── integrations/followupboss  (FUB REST)        │
                      │      │        ├── integrations/sierra_crm    (Sierra REST,     │
                      │      │        │        CRM_VENDOR-selected — ADR-0016)         │
                      │      │        └── integrations/vapi          (Vapi REST)       │
                      │      ▼                                                         │
                      │  Cloud SQL Postgres ── domain tables + engine state tables     │
                      │  Secret Manager ── per-client keys (pushed out-of-band)        │
                      └────────────────────────────────────────────────────────────────┘
```

Each external-API integration ships three things: an **adapter** (implements a core
port over the real API's shapes), a **stub** (recorded-shape double for demo mode and
contract tests), and an **MCP server** (the same operations as MCP tools). The sentinel
base URL `internal` mounts the stub in-process over an ASGI transport — which is how the
demo client deploys to Cloud Run as a single container with zero secrets. (The
extraction integrations are the exception: `llm_extraction` and `pydantic_ai_extraction`
are adapters behind `ExtractionPort` selected by the explicit `EXTRACTION_BACKEND`
config, with no stub or MCP server — see ADR-0006/ADR-0014.)

## Monorepo layout

```
core/                    # framework-free domain: models/, services/, ports/
integrations/
  mls_reso/              # mock RESO Web API + adapter + MCP server
  followupboss/          # FUB adapter (token-bucket rate limited) + stub + MCP server
  sierra_crm/            # Sierra Interactive adapter + stub + MCP server (ADR-0016)
  vapi/                  # voice adapter + webhook-firing stub + MCP server
  twilio_sms/            # Twilio SMS adapter + recorded-shape stub + MCP server (ADR-0017)
  llm_extraction/        # Claude extraction adapter behind ExtractionPort (ADR-0006)
  pydantic_ai_extraction/  # PydanticAI extraction adapter, same port (ADR-0014)
  google_oidc/           # Google ID-token verifier behind IdentityVerifier (ADR-0007)
  email_smtp/            # SMTP EmailSender adapter for magic-link delivery (ADR-0008)
orchestration/
  langgraph/             # the engine: graphs/, checkpointer (state schemas in core)
api/                     # FastAPI: routes, webhooks, cron, workflow engine, Alembic
frontend/                # React + Vite: Listings, Transactions, Approval Inbox, role-shaped homes (deadline queue, search)
infra/                   # Terraform: per-client module, client tfvars, bootstrap
docs/                    # this file, DEMO.md, ADRs/
```

## The three workflows

**`listing_to_contract`** — intake → eligibility check → deterministic marketing
draft → a pause at a human approval gate → approved drafts fan out into real
CRM tasks through `CRMPort`. Rejections end cleanly; approvers can edit the draft in
the approval payload.

**`transaction_coordination`** — scheduled, not user-triggered: Cloud Scheduler hits
`/internal/cron/milestones`, which runs one workflow per active transaction. The
`milestone_engine` core service owns every deadline rule (due-soon windows, severity
ordering, external-blocker override); the workflow only routes on its results. Near
deadlines send CRM reminder tasks; external blockers queue voice-call intents;
overdue milestones pause at an escalation gate — approval creates an URGENT CRM task
and ratchets the milestone's escalation level. The cron run skips transactions with
a pending escalation, so gates never stack. Active transactions are opened by
`POST /transactions` (operator-gated): it validates the escrow dates and generates
the milestone timeline from a per-client template (`milestone_schedule`) before
persisting through the `TransactionStore` port.

**`vapi_followup`** — webhook-driven: a completed feedback call's end-of-call report
drives ingest → structured extraction → persisted feedback → CRM sync (note + call
log). Offer-intent signals pause at a notify-agent gate that creates a hot-lead task
on approval. Extraction sits behind `ExtractionPort` (ADR-0006): a deterministic
keyword/parser default (zero-credential, including a spoken price-range parser,
"four fifty" → $450,000) and two LLM backends — a raw-SDK Claude adapter (ADR-0006)
and a PydanticAI agent adapter with validation self-correction and per-run usage
limits (ADR-0014) — selected per-client by the explicit, fail-loud
`EXTRACTION_BACKEND` config. The `ExtractedFeedback` Pydantic schema is the contract
for all of them (ADR-0002), phrased once as a shared prompt in core (ADR-0005).

## The HITL contract

Every human gate in every workflow passes through the same spine:

1. A node raises an interrupt with a payload (LangGraph `interrupt()`) — the run
   persists its state and stops.
2. The workflow engine (the **only** place interrupts are handled) writes an
   `ApprovalRequest` row: workflow, thread id, kind, payload.
3. The Approval Inbox renders pending requests; kind-specific cards preview the
   decision (marketing draft, overdue milestones, hot-lead summary).
4. `POST /approvals/{id}/decide` resumes the thread with the decision. One endpoint,
   every workflow.

Because state lives in Postgres and approvals are rows (not in-memory futures), a
pause costs nothing and survives anything short of losing the database.

This uniformity is what keeps the orchestrator behind a thin seam. Nodes contain no
business logic, all external calls go through ports, all HITL passes through
`ApprovalRequest` rows, and state schemas are Pydantic models in core — so the
LangGraph engine is a shell of run/pause/resume mechanics with nothing domain-specific
in it. A second orchestration engine once ran the same three workflows behind this same
protocol, proving the seam holds; the product settled on the single LangGraph engine
and retains the seam (ADR-0019, superseding ADR-0004). CI runs the e2e demo script on
every push; the deterministic workflows need no LLM key.

## Operator authentication & access control

Auth follows the same hexagonal discipline: `core/` defines an `IdentityVerifier`
port producing a `Principal`, and adapters prove identity however a deployment
chooses. It is **opt-in** — with nothing configured, a `DemoIdentityVerifier` returns
a fixed operator so `docker compose up` stays login-free.

- **Methods, selectable per deploy** (`AUTH_METHODS`): Google OIDC (verify the ID
  token against Google's certs, ADR-0007) and magic-link email (single-use,
  SHA-256-hashed, 15-minute token, ADR-0008). A self-issued **session JWT** is the
  unifying bearer, so a `CompositeIdentityVerifier` treats both behind one
  `verify()`. Who may sign in is a shared **email allowlist** (domain and/or explicit
  list), enforced identically by both methods. Magic-link delivery is an
  `EmailSender` port — a console default (logs the link) and an SMTP adapter for real
  delivery through any provider.
- **Roles (ADR-0009).** A `Principal` carries a hierarchical role
  (`viewer < operator < admin`), assigned from config by a `RoleResolver` and carried
  in the session JWT — no per-request lookup. A `require_role` dependency gates the
  privilege-sensitive routes: admins decide approvals, operators also start workflows
  and place calls, viewers read. Like the allowlist it is opt-in — no role config
  means every signed-in operator is an admin (pre-RBAC behavior). The frontend reads
  the role from `/auth/me` to hide controls it can't use, but the API is the
  authority: a route a role can't reach returns 403 regardless of the UI. A bearer
  whose role claim is missing or unrecognized resolves to `viewer` (least privilege),
  so a stale pre-RBAC token can never imply write access.
- **Token refresh (ADR-0013).** The session bearer is a short-lived **access** JWT
  (1h); login also mints a longer-lived **refresh** JWT (24h) the SPA exchanges at
  `POST /auth/refresh` for a new access token, so an operator isn't bounced
  mid-session. Refresh is stateless and never extends the refresh token's own TTL, so
  a leaked credential is bounded and can't be renewed indefinitely; every refresh
  re-checks the allowlist and re-resolves the role, so a revoked or demoted operator
  loses access within one access lifetime. A refresh token is rejected as a request
  bearer (a distinct `typ` claim), and Google logins — whose ID token is Google's to
  renew — simply re-prompt on expiry.

Secrets stay out of the repo as everywhere else: the session signing key is
Terraform-generated, OIDC client ids are public env, and any SMTP password is pushed
to Secret Manager out-of-band.

The machine and demo edges fail closed rather than open. The Vapi webhook — which can
start a workflow run — rejects any request unless a real `VAPI_WEBHOOK_SECRET` is
configured; an empty value or the repo-known `"unset"` Terraform placeholder is treated
as unconfigured (500), so a misconfigured deploy can't be driven with a secret anyone
could read from the repo (Terraform seeds a generated value, never `"unset"`). The
`/demo/*` seed/reset surface can drop a tenant's transactions, so it is mounted **only**
when `ENABLE_DEMO_ROUTES` is set (the bundled demo opts in; a real client deploy never
does) — when off the routes are absent entirely: no OpenAPI entry and any method returns
`404`, indistinguishable from a route that never existed.

## Action audit ledger

Approvals record *decisions* and tracing records *execution for debugging*, but
neither is a trustworthy business history of what the system actually did to a
client's external systems. The audit ledger fills that gap (ADR-0010): every write
that crosses the MCP boundary produces exactly one durable `MutationRecord` —
success or failure — capturing the tool, integration, workflow run, originating
`ApprovalRequest` (when gated), initiating actor, secret-redacted arguments,
outcome, and external id or error.

It honors the same seams. The record type is a `core/` Pydantic model and the
`AuditLog` is a `core/ports/` Protocol (SQL-backed `SqlAuditLog` in `api/`, alembic
`0005`; an `InMemoryAuditLog` for tests). Recording happens at a **single seam** —
`RecordingCRM`/`RecordingVoice` port decorators wired once in `main.py` — so recording
happens below the engine, with no engine-specific callback; per-run context
(workflow run id, approval id, actor) flows through a `ContextVar` the engine
publishes at its run boundary. `GET /audit?workflow_run_id=` reads the trail and a
React Audit-trail tab browses it. Demo mode still produces records against the stub
integrations with zero credentials, and the trail survives a mid-run restart.

## Idempotent writes

Agents retry and an orchestrator can re-run a node on resume, so the same write must
not produce a duplicate side effect — a second CRM task, or worst of all a second
outbound call. A second decorator on the same write-boundary seam makes every external
write idempotent (ADR-0011). The wiring is `Idempotent(Recording(adapter))`: the
idempotency wrapper is *outermost*, so a deduped replay short-circuits before the audit
ledger — it performs no side effect and writes no second `MutationRecord`.

The key convention is a `core` service: a stable SHA-256 over
`(workflow_run_id, tool, semantic-args)`, where the run id comes from the same
`audit_scope` ContextVar the ledger uses, so the two seams agree on what "this run" is.
The `IdempotencyStore` is a `core/ports/` Protocol; the API supplies
`SqlIdempotencyStore` (an `idempotency_keys` table, alembic `0006`, whose primary-key
insert is the atomic claim) and an in-memory twin. A write claims its key before the
side effect and records the original result on success; a completed-key replay returns
that result without re-executing — at most once. The narrow mid-write-crash window
(claimed but not yet recorded) refuses to repeat rather than risk a duplicate. Both
engines inherit this identically, demo stubs included.

## Tenant isolation

One compromised or prompt-injected agent must never reach another brokerage's data, and
that guarantee can't live in the prompt — it lives below the agent (ADR-0012). The tenant
is bound from deploy config (`TENANT_ID`, the client identity) around every request by a
pure-ASGI middleware, carried on a `ContextVar` like the audit seam, and read by the data
layer — no core service or MCP tool takes a tenant parameter, so the agent has no "which
brokerage" knob. Scoped store wrappers (`core/services/scoped_stores.py`) stamp the bound
tenant on writes, deny by-id access to another tenant's rows, and record a denied attempt
in the audit ledger as a `security` event. A forced Postgres row-level-security policy
keyed on a per-transaction GUC (alembic `0007`) is the database belt under the app layer —
it binds under a non-owner, non-`BYPASSRLS` DB role, which GCP deploys now use for the
runtime connection (`brokerops_app`, BOP-013/ADR-0021); the owner role is kept only for
schema management (alembic + the checkpointer). Under the single superuser role of
compose/CI, RLS is inert and the app-layer wrapper is the always-on guarantee. brokerops is
single-tenant *per deploy* today, so the wrapper makes that boundary explicit and a future
shared-database migration mechanical. That was increment 1. Increment 2 (BOP-011,
`core/services/tool_authz.py`) authorizes every tenant-bearing tool *input* at the entry
point, before any port/store method runs. Increment 3 (BOP-012,
`core/services/egress.py`) scans every tool *response* before it leaves the boundary: a
foreign tenant identifier blocks the whole response fail-closed (logged and recorded as a
`security` event — tool + finding class, never the payload), secret-shaped values and
role-restricted PII redact in place with a marker, and the rules are data — the shared
tenant-value extraction, the audit trail's secret-key hints plus a value-shape pattern
table, and `Pii` field annotations on the response models — never per-tool code. Both
directions ride one wrapper (`guard_tool_ports`) on every port in
`app.state.engine_tool_ports`, and the enumeration test asserts both markers on every
registered port, so a new tool entry point cannot ship unguarded in either direction.
The always-on covered surface is the engine tool seam, filtered at the pinned OPERATOR
tier regardless of caller. HTTP route responses are RBAC-scoped (`require_role`,
ADR-0009); per-caller-role egress filtering of read responses
(`scrub_payload(recipient_role=…)`) shipped for the transaction hub reads in BOP-027 and
extends to the role-shaped home reads (`GET /transactions/deadlines`,
`/transactions/search`) in BOP-030 — so a viewer receives party names but not their
contact emails. Bringing the remaining store-backed reads under the same per-caller
filter is tracked in BOP-040. Per-agent
least-privilege infrastructure shipped in BOP-013 (ADR-0021): the non-owner runtime DB
role above, plus per-client cloud isolation (own GCP project + per-secret IAM). Increment 4 (BOP-020) closes the
*outbound* direction the result filter does not cover: `MessageSendService` runs every
outbound message through the same secret-shape redaction both when a draft is persisted for
approval and immediately before `port.send`, so LLM-drafted copy cannot carry a leaked
credential to the approval card or an external provider — and the LLM drafting backend is
wiring-gated onto guarded channel seams only (email + SMS).

## Data

Cloud SQL Postgres (one small instance per client — cheap isolation; consolidation
to a shared instance is a DSN change). Alembic migrations run on container start.
Domain tables: approval_requests, transactions, milestones, call_records,
showing_feedback, magic_login_tokens, mutation_records, idempotency_keys. The engine
manages its own state tables alongside (LangGraph's checkpointer).

Contacts are deliberately **not** a domain table: the CRM is the source of truth and
contacts are read-through DTOs via `CRMPort`. Caching policy is ADR-0001: a thin
`CachePort` (in-memory default; Redis/Memorystore behind a Terraform flag) for
rate-limit protection and voice-path reads — never for anything HITL-adjacent.

## Deploy

`make deploy CLIENT=acme VERSION=vX.Y.Z` applies a per-client Terraform module: two
Cloud Run services (pinned to a git-tagged release image — ADR-0025), Cloud SQL (an
owner DB role for migrations + a non-owner least-privilege runtime role, BOP-013),
Secret Manager shells (real keys pushed interactively by `make secrets` — values
never touch the repo or state), the milestone Scheduler job, and least-privilege
service accounts. Per-client tfvars are committed and contain no secrets (not even an
image path — the registry ref is derived from the version); Terraform state lives in
GCS with a per-client prefix.

Hard-won deploy details are encoded in the module: Cloud SQL's edition pin for
shared-core tiers, real `.uri` references instead of predicted URLs (ADR-0003), and
external health checks on `/readyz` because Google's frontend reserves `/healthz` on
`*.run.app` hosts. Per-instance detail (version, selectors, migration pin, last cron
outcome) lives on authenticated `/statusz` — see [monitoring.md](monitoring.md) (BOP-035).

## Verification

OData contract tests pinning the RESO subset, adapter tests against the
stubs (the same shapes the real APIs return), workflow tests for every branch of all
three workflows, API-level flow tests, auth tests (allowlist,
magic-link lifecycle, session-JWT round-trip, role resolution, and `require_role`
enforcement), audit-ledger tests (deep secret redaction, success-and-failure
recording, restart survival), idempotency tests (replay performs the
side effect at most once and returns the original result, atomic claim, restart
survival), transaction-open tests (deterministic bounded id, idempotent open with
conflict and race handling, escrow-date validation, engine wire-through), tenant-scoping
tests (cross-tenant by-id denial, write stamping, denied-attempt audit events, RLS belt),
tool-seam tests (input authorization plus egress filtering: cross-tenant response
blocking, secret-shape and role-restricted-PII redaction, both-direction enumeration
over the engine tool-port registry),
extraction-selector tests (deterministic default, fail-loud on an explicitly selected
LLM backend with no key, fail-closed on unknown values) plus an offline PydanticAI
adapter suite (TestModel, no credentials),
a Postgres restart-survival proof (runs in CI against a service container), and a
scripted e2e demo check that CI runs against the full compose stack. The frontend has its own vitest
suite (role-gating on the Approvals inbox and Listings board, `apiFetch` bearer/401
handling, and the `AuthProvider` bootstrap phases), type-checked and run as a separate
CI job. Ruff + mypy strict across the workspace; gitleaks on every commit and in CI.
