# ADR-0026: Deployment & tenancy posture — A1 default, B enterprise tier, A2 deferred

**Status:** Accepted · **Date:** 2026-07-31 · **Relates to:** [ADR-0012](ADR-0012-tenant-scoping.md) (tenant scoping / single-tenant-per-deploy), [ADR-0021](ADR-0021-least-privilege-runtime-db-role.md) (per-project isolation + runtime RLS role), [ADR-0025](ADR-0025-versioned-releases.md) (fleet release foundation)

**Originating decision record:** the originating internal design record for the deployment model (§2 accepted 2026-07-05; this ADR is the public, repo-local half of that ratification).

## Context

How brokerops deploys many times is a product decision, not only an infra one:
multi-tenant SaaS in our cloud vs. per-client deploys on client infrastructure vs.
something in between. The repo already ships a per-client Terraform module, Secret
Manager shells, per-client tfvars, and ADR-0012's single-tenant-**per-deploy**
line — but the *posture* (what we sell by default, what is enterprise, what is
deliberately deferred) lived only in the originating internal design record. Case-study readers of
this public repo need the same decision on the ADR shelf.

Three postures were considered (names from the deployment-model design):

| | **A1** — dedicated instance, our cloud | **A2** — shared multi-tenant, our cloud | **B** — client infrastructure |
|---|---|---|---|
| What it is | One GCP project per brokerage (`make deploy CLIENT=x`), monthly service fee | One app + one DB, tenant column, monthly fee | Same Terraform module applied into the client's GCP org; service contract |
| Start cost | None structural — already how the repo deploys | Real: shared-DB ops, credential mgmt, noisy-neighbor, harness depth | No code fork; ops hours and foreign-cloud friction per client |
| Isolation | Strongest — project boundary + per-client SAs (ADR-0021) | One bug or prompt-injected agent can span clients unless the harness carries everything | Strong in their org; our access surface is the risk |

## Decision

1. **A1 is the product.** New clients are `make deploy CLIENT=<slug>` into a
   WebDrvn-owned GCP project, billed as a monthly service fee. One brokerage, one
   project, one Cloud SQL instance, one secret set, per-client service accounts.
   The sales story is a dedicated private instance and database — not a compromise.

2. **B is a priced-up enterprise tier on the same module, with no code fork.** Point
   the existing Terraform module at a client-owned project; price covers the ops
   asymmetry (their credentials, change windows, billing). If a B client needs a
   code change, it ships to the whole fleet or not at all.

3. **A2 (true multi-tenancy) is deferred**, with a **maintained migration escape
   hatch** so a future shared-DB move stays mechanical rather than a rewrite.
   Revisit when the fleet is roughly 30–50 active clients, or a unit-economics
   forcing event appears. Until then A2's only payoffs (marginal infra cost, one
   upgrade surface) do not bind at the 1–20 client scale, and its security bar is
   more cheaply *not needed* than built.

### Why A1

- The repo is already built for it (per-client module, tfvars, secrets, ADR-0012
  single-tenant-per-deploy).
- Dedicated instances are a selling point to brokerages, not overhead to apologize
  for.
- The Cloud SQL / Cloud Run floor is noise against any plausible monthly fee until
  the fleet is large.

### A2 escape-hatch invariants

Every future schema and service change **must** preserve the path to a shared
database. These are non-optional while A2 remains deferred:

1. **Tenant columns + forced RLS** on every agent-reachable table (ADR-0012,
   alembic `0007` and successors; runtime role cannot shed the belt — ADR-0021).
2. **Tenant in derived-id hashes** wherever an id is computed from external or
   natural keys, so a shared-DB world cannot collide two brokerages' rows.
3. **Scoped stores** (`core/services/scoped_stores.py`) as the always-on app-layer
   guarantee: stamp bound tenant on writes, deny by-id foreign-tenant access,
   fail closed when unbound — no core service or MCP tool takes a tenant parameter.

### Fleet-ops lane (how we "deploy many times")

Fleet operations are identical under A1 and B and are what makes many instances
operable. Shipped (or shipping) as **BOP-031..036**:

| Task | Surface |
|---|---|
| BOP-031 | Versioned releases — git tags → tagged images; deploy pins `VERSION` (ADR-0025) |
| BOP-032 | Fleet registry — opaque slug + non-identifying fields; identifying overlay gitignored |
| BOP-033 | Fleet upgrade driver — plan → apply → verify per client, stop on failure |
| BOP-034 | Terraform selector-parity check — every selector wired in `services.tf` |
| BOP-035 | Per-instance health surface — `/readyz`, `/statusz`, cron outcome, structured logs |
| BOP-036 | Offboarding path (destroy + export) when filed |

## Consequences

- (+) Public readers of this repo see the same A1/B/A2 posture as the internal
  design doc, without client-identifying or fee detail.
- (+) A1 keeps blast radius at the project boundary; the in-code tenant belt
  (ADR-0012/0021) remains the explicit guarantee and the A2 prep, not dead weight.
- (+) B does not fork the product; enterprise isolation is a deploy target, not a
  branch.
- (−) True multi-tenant SaaS economics stay off the table until revisit criteria
  hit; schema/service authors must keep the escape-hatch invariants forever until
  then.
- (−) Fleet ops (registry, upgrade driver, health) are required engineering under
  A1 — already the fleet-ops lane, not a new commitment of this ADR.

## Non-goals (v1)

A2 implementation work; self-serve signup/billing; multi-region; SLA tooling beyond
the public health hooks; white-label beyond existing UI token theming.
