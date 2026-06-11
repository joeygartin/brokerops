# ADR-0001: Defer Redis Iris; use a thin CachePort with pluggable backends

**Status:** Accepted · **Date:** 2026-06-09

## Context

We need caching for two concrete reasons: (1) FollowUpBoss enforces strict API rate
limits, so repeated contact/search reads must be deduplicated; (2) live data lookups
during an active voice call are latency-sensitive. The HITL workflow graphs themselves
are not latency-sensitive — they checkpoint and wait on human approval for minutes to
days.

Redis launched **Iris** (May 2026), a fully-managed context-engine suite on Redis
Cloud: LangCache (semantic LLM caching), Agent Memory (session + long-term agent
memory), Context Retriever (auto-generates MCP tools from business data models), and
Data Integration (CDC sync from relational sources). We evaluated it as the
caching/context layer for this system.

## Decision

Defer Redis Iris. Implement a thin `CachePort` protocol in `core/ports/` with two
backends:

- `InMemoryCache` (LRU + TTL) — default for demo mode and tests; zero dependencies.
- `RedisCache` — optional; plain Redis via docker-compose locally, GCP Memorystore
  behind the `enable_redis` Terraform flag for client deploys that need it.

Cache scope (V1): FUB contact/search reads (TTL 5 min), MLS search results (TTL 15
min), single listings (TTL 5 min). Nothing HITL-adjacent is ever cached.

## Rationale

1. **Wrong tool for the stated need.** Our need is read-through caching of
   third-party API responses — classic Redis/Memorystore territory. Iris's value
   proposition is agent *context* (semantic caching of LLM responses, persistent
   agent memory, auto-generated tools), which V1 does not require.
2. **Conflicts with the project's centerpiece.** Iris's Context Retriever
   auto-generates MCP tools from data models. Our hand-built MCP integration layer
   (mock RESO, FollowUpBoss, Vapi) is the architectural showcase of this repo.
   Outsourcing tool generation would hollow out the thing the project exists to
   demonstrate.
3. **Breaks demo-mode goals.** Iris is managed-only (Redis Cloud). A per-reviewer or
   per-client SaaS account violates the "clone → docker compose up → zero
   credentials" requirement and complicates the per-client Terraform story.
4. **No speculative infrastructure.** The `CachePort` abstraction means any future
   backend swap (including Iris components) is an adapter, not a refactor.

## Revisit triggers

Re-open this decision when any of the following becomes true:

- **LLM token costs become material** (e.g., > ~15% of per-client infra cost) with
  high query similarity → evaluate **LangCache** specifically.
- **Cross-session agent memory** becomes a product requirement (e.g., persistent
  caller memory across voice calls) → evaluate **Agent Memory** vs. extending our
  Postgres domain model.
- **A live MLS feed replaces the mock** and read volume requires CDC-style sync
  rather than TTL caching → evaluate **Data Integration** vs. a scheduled sync job.
- Iris (or components) becomes self-hostable or available via GCP Marketplace with
  Terraform support.

## Consequences

- (+) Zero new SaaS dependencies; demo mode stays credential-free.
- (+) Rate-limit protection and voice-path latency handled with ~100 lines of code.
- (+) This ADR itself documents frontier-tracking with restraint.
- (−) No semantic LLM caching in V1; accepted, as graph prompts are low-volume and
  heterogeneous.
- (−) If revisit triggers fire, integration work returns — mitigated by `CachePort`
  and the MCP boundary.
