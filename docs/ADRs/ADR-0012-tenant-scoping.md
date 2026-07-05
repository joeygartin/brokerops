# ADR-0012: Tenant scoping (the agent can't reach another brokerage)

**Status:** Accepted · **Date:** 2026-06-28 · **Relates to:** ADR-0010 (audit ledger), ADR-0011 (idempotent writes), ADR-0004 (dual engines)

> **Historical framing (see [ADR-0019](ADR-0019-one-orchestrator-langgraph.md)).** This
> ADR was written while two orchestration engines ran side by side (ADR-0004). brokerops
> has since committed to a single LangGraph engine and removed the ADK lane. The
> tenant-scoping seam and property described below still hold — they sit below the one
> engine, exactly as they did below two. Read "two engines" as the state at this
> decision's date.

## Context

brokerops is a multi-tenant agent backoffice. One compromised or prompt-injected
agent must never read or mutate another brokerage's data. The guarantee cannot live
in the prompt — the prompt is attacker-influenceable the moment an agent reads an MLS
note, a CRM message, or any external text. Enforcement must live **below** the agent,
in code the model cannot rewrite.

The strongest boundary already exists: each client deploys to its own GCP project,
its own Cloud SQL instance, and its own database (single-tenant **per deploy**). What
was missing was an *in-code* tenant boundary at the data/tool layer — so the guarantee
is explicit and enforced rather than incidental, and so a future consolidation to a
shared database is mechanical rather than a rewrite. As with ADR-0010/0011, the
shaping constraint is the two engines (LangGraph, ADK) behind one `WorkflowEngine`
seam: a mechanism in engine code would be built and kept in sync twice.

## Decision

Bind the tenant **below the agent** at the request boundary and read it from there in
the data layer — never from a method argument or model state. No core service or MCP
tool accepts a tenant parameter; the agent has no "which brokerage" knob.

1. **Seam in `core` (rules #1–#3).** `core/services/tenancy.py` carries a `ContextVar`
   (`tenant_scope` / `require_tenant`, fail-closed) — the same pattern as the audit
   seam (ADR-0010). `TenantScopeMiddleware` (a pure-ASGI middleware) binds
   `tenant_scope(TENANT_ID)` around every request, so the engines need no change and
   the binding propagates into the workflow run task. `TENANT_ID` is the deploy's
   client identity (Terraform sets it from the client name; default `demo` locally).
2. **Scoped data-access wrappers (req: scoped repository).** `core/services/scoped_stores.py`
   decorates the persistence ports: writes stamp the bound tenant and **deny + audit**
   a record carrying a *foreign* tenant id; by-id reads/mutations refuse another
   tenant's row; unscoped access fails closed. A denied attempt is recorded in the
   action-audit ledger (ADR-0010) as a `security` event — no new schema.
3. **Row-level-security belt.** Alembic `0007` adds `tenant_id` to the agent-reachable
   tables and a forced RLS policy keyed on a per-transaction GUC the app binds, so a
   scope missed in the app is still denied at the database.

## Consequences

- A turned agent's blast radius is one tenant, never the fleet — enforced in code on
  every backend, with RLS as the database belt.
- **RLS engages under a least-privilege (non-superuser) DB role.** A Postgres superuser
  bypasses RLS even with `FORCE`; the demo/compose role is a superuser, so there the
  app-layer wrapper is the always-on guarantee and RLS is inert. Provisioning the
  least-privilege role is a deferred follow-on (with per-agent IAM hardening).
- **Audit scope is bounded** (ratified 2026-06-28): cross-tenant access is denied on
  every path; the *security-event audit* is reliable on the write path (foreign model
  tenant id, all backends) and the app-visible by-id paths. A by-id read hidden by RLS
  under a hardened role is denied at the database but not separately audited — logging
  it would require confirming the row exists under another tenant, which is itself the
  leak (and under per-deploy tenancy no foreign rows exist to read). A `tenant_scope`
  re-bind is hard-rejected but not a node-reachable surface. Full audit coverage for a
  shared database is part of the deferred follow-on increments.
- This is increment 1 of the capability-security foundation; follow-ons (filed
  separately): uniform tool-level authorization, output/egress filtering, and
  per-agent least-privilege service accounts.
