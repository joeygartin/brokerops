# ADR-0018: Generated TypeScript API client with a CI contract-drift gate

**Status:** Accepted · **Date:** 2026-07-05

> Numbering note: authored on a parallel branch (BOP-023); a concurrent branch may
> also claim ADR-0018 — renumber at integration if it collides.

## Context

The frontend's `types.ts` was hand-written and silently drifted from the API: a
Pydantic response-model change compiled fine on both sides and only failed in the
browser. The repo's one-model thesis already makes the Pydantic model the single
source of truth for validation, JSON Schema, and serialization — but the wire
contract to the SPA was re-declared by hand on the other side of HTTP.

FastAPI emits the OpenAPI projection of those models for free (`app.openapi()`), and
mature generators can turn that spec into TypeScript types plus a typed SDK. The open
questions were where generation runs (committed artifacts vs generate-in-CI), which
generator, and how the existing auth behavior (`apiFetch`: bearer header, single
shared 401 refresh-and-replay, session-generation guards — ADR-0013) survives the
move to a generated call layer.

## Decision

1. **The spec is exported deterministically and committed.**
   `scripts/export_openapi.py` dumps `app.openapi()` to `frontend/openapi.json`
   (sorted keys, stable formatting). The script pins the one env knob that changes
   the mounted route surface (`ENABLE_DEMO_ROUTES=1`), so output is reproducible
   from any shell. The exported contract deliberately includes the demo surface: the
   demo UI calls it, and a client deploy simply has no such routes (they 404).

2. **The client is generated with `@hey-api/openapi-ts` (pinned exactly, 0.99.0)
   into `frontend/src/client/` and committed.** Committed-generated beats
   generate-on-install here: reviewable contract diffs in PRs, frontend builds with
   no Python toolchain, and one canonical artifact CI can byte-compare. The version
   is pinned exactly (not a range) because generated output is diff-gated — even a
   patch-level codegen change would fail the gate on an unrelated PR. Regeneration
   is `make generate` (spec + client) or `npm run generate` (client from committed
   spec). Nothing under `src/client/` is hand-edited.

3. **CI enforces no drift.** The `contract-drift` job regenerates spec + client and
   fails on `git diff --exit-code` (plus an untracked-file check) over
   `frontend/openapi.json` and `frontend/src/client/`. A backend contract change
   that skipped regeneration — or a hand-edit to generated code — cannot merge.

4. **`apiFetch` is the generated client's fetch layer.** The client is configured
   once (`src/heyApiConfig.ts`, applied at client creation via the generator's
   `runtimeConfigPath`) with `fetch: apiFetch`, so every generated call inherits the
   bearer header and the 401 refresh-and-replay/session-generation behavior
   unchanged. `apiFetch` now also accepts a `Request` (the client hands it one),
   cloning per attempt so the single-use body survives the replay; the URL-string
   path is byte-for-byte what it was, proven by the pre-existing auth vitest suite.

5. **What stays frontend-owned, and why.** `ApprovalRequest.payload` is an open
   dict on the backend by design (one HITL spine, kind-discriminated at runtime), so
   its typed view lives in `ApprovalsInbox.tsx` as a cast. `Role`/`roleAtLeast`
   moved to `src/roles.ts` because `/auth/me` is typed `dict[str, object]` on the
   backend — the spec carries no Role schema to generate from (typing that response
   is the natural follow-up that would delete this module). The cron trigger stays
   on plain `fetch`: it is gated by `X-Cron-Key`, not the bearer, and its 401 must
   not trip the session-teardown path.

## Consequences

- (+) A Pydantic wire-shape change the frontend hasn't absorbed is now a red CI
  check, not a runtime surprise; `types.ts` is deleted.
- (+) Views get real request/response/enum types (e.g. `DocumentKind` options are
  enumerated from the generated runtime enum, never hand-copied).
- (+) The whole API surface (messages, documents, webhooks, drafting) is callable
  and typed the moment it exists in the spec — nothing hand-curated.
- (−) Every backend contract change now requires `make generate` in the same PR;
  the drift job makes forgetting cheap to discover but it is still a step.
- (−) Generator upgrades regenerate the world in one diff; the exact version pin
  keeps that a deliberate, isolated change.
- (−) Weakly-typed backend responses (`/auth/me`, cron summary) generate weak TS
  types; they keep local casts until the backend types them.
