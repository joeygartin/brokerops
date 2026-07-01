# ADR-0013: Session token refresh — short access token + refresh token

**Status:** Accepted · **Date:** 2026-07-01 · **Builds on:** ADR-0008, ADR-0009

## Context

ADR-0008 made a self-issued **session JWT** the unifying bearer for non-OIDC logins
(magic link), and ADR-0009 carried the operator's role inside it. That token had a
single 8-hour TTL and no renewal path: when it expired, the API returned 401, the SPA
cleared the session, and the operator had to request a fresh magic link (or re-run
Google Sign-In). For an all-day operator console that is a real papercut — a shift
outlasts the token — and it pushed the TTL long (8h) precisely to hide the seam, which
enlarges the window a stolen bearer stays valid.

We want the operator to stay signed in across a working session without re-login,
*and* a short-lived request bearer, without adding a server-side session store (the
deploy-per-tenant model and zero-credential demo both favor stateless verification).

## Decision

Split the one long-lived token into two, both stateless HS256 JWTs signed with the
same key, distinguished by a `typ` claim:

1. **Access token (`typ=access`, 1h).** The request bearer. `SessionTokenVerifier`
   requires `typ == "access"` exactly and **rejects a refresh token — or any other/no
   `typ` — presented as a bearer** (closed allow-list, fail-closed): a renewal
   credential must never authorize a protected route, and a pre-split token simply
   re-logs in once on deploy rather than being grandfathered in.
2. **Refresh token (`typ=refresh`, 24h).** Issued alongside the access token at login.
   The SPA exchanges it at `POST /auth/refresh` for a fresh access token. The endpoint
   is open (the refresh token *is* the credential), re-validates the token, and
   **re-checks the allowlist and re-resolves the role from the email** on every call —
   so a de-allowlisted or demoted operator loses (or has downgraded) access within one
   access lifetime (≤1h), not at the next full login.
3. **No refresh-token rotation.** Refresh returns only a new access token; the refresh
   token's own TTL is never extended. The absolute session length is therefore bounded
   by the 24h refresh TTL, and a leaked refresh token cannot be renewed indefinitely
   (fail-closed, consistent with BOP-007). Re-login is required once per 24h.
4. **Frontend, reactive.** `apiFetch` attaches the access bearer; on a 401 it tries
   `refreshSession()` once and replays the request on success, otherwise clears the
   session and re-prompts. Concurrent 401s share a single in-flight refresh. Both
   tokens live in `sessionStorage` (never past the browser session), matching the
   existing posture. A monotonic **session generation** (bumped on every login and
   sign-out) guards the boundary: a request or refresh that resolves after the session
   it belonged to was replaced or cleared is discarded — it can neither resurrect a
   signed-out session, replay under a newer login, nor tear the newer session down.
   The Google path stores the ID token as access-only and clears any refresh token, so
   a stale magic refresh can never renew a Google login.

The seam is unchanged where it matters: `MagicLinkService.redeem` returns a
`SessionTokens(access, refresh)` pair (core gains `SessionIssuer.issue_refresh`);
refresh mechanics (JWT, `typ`, `SessionRefresher`) live entirely in the api adapter.
Refresh applies only to the api-issued session JWT — a **Google ID token is Google's
to renew**, so `/auth/refresh` is absent (404) unless the magic method is enabled, and
Google logins re-prompt on expiry as before. Demo mode (auth off) issues no tokens and
is unaffected.

## Consequences

- Operators stay signed in across a working session with a 1h request-bearer exposure
  window instead of 8h, and revocation (allowlist/role) now takes effect within ≤1h.
- Statelessness is preserved — no session table, no new migration, no per-request
  lookup. The cost is no server-side revocation *before* the refresh token's 24h TTL;
  acceptable for an allowlisted operator console and bounded by the no-rotation cap. A
  future upgrade path is a stored, rotating refresh token (single-use with reuse
  detection) if per-token revocation is ever needed.
- Access TTL (1h) and refresh TTL (24h) are code constants, not new env/TF vars — no
  configuration surface added, and the zero-credential contract is untouched.
