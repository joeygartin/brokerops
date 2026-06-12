# brokerops — Architecture

AI-powered backoffice for real estate brokerages: listing-to-contract marketing,
deadline-driven transaction coordination, and voice follow-up — every consequential
action gated by human approval.

Built as a deliberate demonstration of production agent architecture: the same three
workflows run on either of two orchestration engines — LangGraph and Google ADK —
behind one seam, with durable human-in-the-loop, an MCP tool boundary, hexagonal
domain isolation, and per-client GCP deploys via Terraform.

## Principles

1. **Hexagonal core.** All domain logic lives in `core/` as plain Python + Pydantic.
   `core/` imports no LangGraph, no ADK, no FastAPI, no SDK. Orchestration
   frameworks are shells around it.
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
5. **State is durable.** Workflow state lives in Postgres under either engine
   (LangGraph's checkpointer; ADK's database session service). A workflow paused on
   a human approval survives restarts, deploys, and Cloud Run cold starts — proven
   by an automated restart test per engine and a live container-restart drill.
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
 Cloud Scheduler ────▶│      ├── orchestration/{langgraph|adk}  (3 workflows each,     │
                      │      │        │      selected by ORCHESTRATOR — ADR-0004)      │
                      │      │        │ ports (Protocol)                               │
                      │      │        ├── integrations/mls_reso      (RESO Web API)    │
                      │      │        ├── integrations/followupboss  (FUB REST)        │
                      │      │        └── integrations/vapi          (Vapi REST)       │
                      │      ▼                                                         │
                      │  Cloud SQL Postgres ── domain tables + engine state tables     │
                      │  Secret Manager ── per-client keys (pushed out-of-band)        │
                      └────────────────────────────────────────────────────────────────┘
```

Each integration ships three things: an **adapter** (implements a core port over the
real API's shapes), a **stub** (recorded-shape double for demo mode and contract
tests), and an **MCP server** (the same operations as MCP tools). The sentinel base
URL `internal` mounts the stub in-process over an ASGI transport — which is how the
demo client deploys to Cloud Run as a single container with zero secrets.

## Monorepo layout

```
core/                    # framework-free domain: models/, services/, ports/
integrations/
  mls_reso/              # mock RESO Web API + adapter + MCP server
  followupboss/          # FUB adapter (token-bucket rate limited) + stub + MCP server
  vapi/                  # voice adapter + webhook-firing stub + MCP server
orchestration/
  langgraph/             # V1 engine: graphs/, checkpointer (state schemas in core)
  adk/                   # V2 engine: workflows/, sessions, interrupts
api/                     # FastAPI: routes, webhooks, cron, workflow engine, Alembic
frontend/                # React + Vite: Listings, Transactions, Approval Inbox
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
a pending escalation, so gates never stack.

**`vapi_followup`** — webhook-driven: a completed feedback call's end-of-call report
drives ingest → structured extraction → persisted feedback → CRM sync (note + call
log). Offer-intent signals pause at a notify-agent gate that creates a hot-lead task
on approval. Extraction is deterministic in V1 behind a Pydantic schema, including a
spoken price-range parser ("four fifty" → $450,000); the LLM upgrade is a one-
function swap (ADR-0002).

## The HITL contract

Every human gate in every workflow passes through the same spine:

1. A node raises an interrupt with a payload (LangGraph `interrupt()`; an ADK
   `RequestInput`) — the run persists its state and stops.
2. The workflow engine (the **only** place interrupts are handled) writes an
   `ApprovalRequest` row: workflow, thread id, kind, payload.
3. The Approval Inbox renders pending requests; kind-specific cards preview the
   decision (marketing draft, overdue milestones, hot-lead summary).
4. `POST /approvals/{id}/decide` resumes the thread with the decision. One endpoint,
   every workflow.

Because state lives in Postgres and approvals are rows (not in-memory futures), a
pause costs nothing and survives anything short of losing the database.

This uniformity is the portability story, and V2 cashed it in: `orchestration/adk/`
implements the same three workflows on Google ADK behind the same `WorkflowEngine`
protocol, and `ORCHESTRATOR=langgraph|adk` selects the engine at startup
(ADR-0004). Nodes contain no business logic, all external calls go through ports,
all HITL passes through `ApprovalRequest` rows, and state schemas are Pydantic
models in core — so the port touched orchestration wiring and nothing else. CI runs
the same e2e demo script against both engines on every push; neither needs an LLM
key for these deterministic workflows.

## Data

Cloud SQL Postgres (one small instance per client — cheap isolation; consolidation
to a shared instance is a DSN change). Alembic migrations run on container start.
Domain tables: approval_requests, transactions, milestones, call_records,
showing_feedback. Each engine manages its own state tables alongside (LangGraph's
checkpointer; ADK's session service).

Contacts are deliberately **not** a domain table: the CRM is the source of truth and
contacts are read-through DTOs via `CRMPort`. Caching policy is ADR-0001: a thin
`CachePort` (in-memory default; Redis/Memorystore behind a Terraform flag) for
rate-limit protection and voice-path reads — never for anything HITL-adjacent.

## Deploy

`make deploy CLIENT=acme` applies a per-client Terraform module: two Cloud Run
services, Cloud SQL, Secret Manager shells (real keys pushed interactively by
`make secrets` — values never touch the repo or state), the milestone Scheduler job,
and least-privilege service accounts. Per-client tfvars are committed and contain no
secrets; Terraform state lives in GCS with a per-client prefix.

Hard-won deploy details are encoded in the module: Cloud SQL's edition pin for
shared-core tiers, real `.uri` references instead of predicted URLs (ADR-0003), and
external health checks on `/readyz` because Google's frontend reserves `/healthz` on
`*.run.app` hosts.

## Verification

~120 tests: OData contract tests pinning the RESO subset, adapter tests against the
stubs (the same shapes the real APIs return), workflow tests for every branch of all
three workflows on **both engines**, API-level flow tests, a Postgres
restart-survival proof per engine (runs in CI against a service container), and a
scripted e2e demo check that CI runs against the full compose stack under both
`ORCHESTRATOR` values. Ruff + mypy strict across the workspace; gitleaks on every
commit and in CI.
