# ADR-0021: Least-privilege runtime DB role + per-agent IAM scoping

**Status:** Accepted · **Date:** 2026-07-07 · **Relates to:** ADR-0012 (tenant scoping), ADR-0010 (audit ledger)

## Context

ADR-0012 gave the agent a tenant boundary in two layers: an always-on app-layer
scope wrapper, and a **forced** row-level-security (RLS) policy in Postgres as the
database belt. That ADR left one edge open and named it a deferred follow-on:

> RLS engages under a least-privilege (non-superuser) DB role. A Postgres superuser
> bypasses RLS even with `FORCE`; … Provisioning the least-privilege role is a
> deferred follow-on (with per-agent IAM hardening).

Until now the app connected to Cloud SQL as `brokerops` — the role that **owns** the
tables and is a `cloudsqlsuperuser` member. `FORCE ROW LEVEL SECURITY` (migration
0007) does bind RLS to the table owner, so the policy was not simply inert on Cloud
SQL. But the owner role can also **turn RLS off** (`ALTER TABLE … NO FORCE / DISABLE
ROW LEVEL SECURITY`), `DROP`/`TRUNCATE` any table including the audit ledger, and run
arbitrary DDL. A compromised or prompt-injected agent running as the owner has that
whole surface. The belt is only as strong as the role that wears it.

## Decision

Split the one DB role into two, and connect the runtime with the weaker one.

1. **Owner / migration role (`brokerops`).** Owns the schema, runs Alembic, and backs
   the LangGraph checkpointer's `setup()` (which creates its own tables). Selected by
   `MIGRATION_DATABASE_URL`. Unchanged privileges.
2. **Runtime role (`brokerops_app`).** A **non-owner, non-`BYPASSRLS`** login role the
   application's tenant-scoped domain stores connect as, selected by `DATABASE_URL`.
   Terraform provisions it (`google_sql_user`); migration **0010** grants it **DML
   only** (`SELECT/INSERT/UPDATE/DELETE`) on an **explicit allowlist** of the runtime
   tables — never `ALL TABLES` or blanket default privileges, so `alembic_version` and
   the checkpointer's own tables stay untouchable — plus `USAGE` on the schema and **no
   `CREATE`**; it best-effort sheds `cloudsqlsuperuser`. A future runtime table adds its
   own one-line `GRANT` in the migration that creates it. Because the role is neither the
   table owner nor a `BYPASSRLS` role, the RLS policy binds to every runtime query
   *regardless* of `FORCE` or platform superuser semantics, and the role **cannot
   disable RLS or run DDL** on the tables it reads.

The two DSNs are Terraform-generated secrets; nothing is pushed by hand. Locally and in
CI `MIGRATION_DATABASE_URL` is unset, so both collapse to the single compose role and
the zero-credential path is byte-for-byte unchanged — migration 0010 is a **no-op when
the runtime role is absent**.

**Per-agent IAM.** The stronger cloud boundary was already in place and is ratified here,
not rebuilt: each client deploys to its **own GCP project**, so its service account, Cloud
SQL instance, database, and secrets are all project-isolated — a turned agent cannot even
name another tenant's resources. Secret access is granted **per-secret** to the api SA
(`secretmanager.secretAccessor` on this client's secrets only), never project-wide; the
sole project-level grant is `cloudsql.client` (the connector, not data access). No shared
service account spans tenants.

## Consequences

- The RLS belt now binds under a role that **cannot take it off** — the database
  guarantee no longer depends on trusting the application not to `ALTER` its own tables.
  Proven live: as `brokerops_app`, cross-tenant reads return nothing, and `NO FORCE`,
  `CREATE`, and `DROP` are all denied while DML succeeds.
- Blast radius of a compromised runtime shrinks from "owner of the database" to "the exact
  operations the app performs on this tenant's rows." Each table is granted only the verbs
  its store executes; the audit ledger (`mutation_records`) is **INSERT+SELECT only**, so a
  compromised runtime can append and read its action history but can never rewrite or delete
  it (ADR-0010), and the schema is no longer app-droppable.
- The checkpointer keeps the owner DSN: workflow-engine state is internal, single-tenant
  per deploy, and not under the tenant RLS policy, so running its `setup()` as owner is
  correct and keeps the runtime role free of `CREATE`.
- Existing deploys need a one-time migration path (add the role, split the DSNs, redeploy)
  — see `docs/deployment-hardening-verification.md`, which also carries the per-deploy
  verification checklist (non-superuser app role, RLS enforced, SA cannot read a foreign
  tenant's secrets).
- This closes the last of the ADR-0012 capability-security follow-ons (uniform tool-level
  authorization, egress filtering, per-agent least-privilege infrastructure).
