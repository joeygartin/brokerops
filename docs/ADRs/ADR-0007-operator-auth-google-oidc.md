# ADR-0007: Operator authentication via Google OIDC behind an IdentityVerifier port

**Status:** Accepted · **Date:** 2026-06-19 · **Relates to:** ADR-0003, ADR-0006

## Context

Through V1/V2 the dashboard and the API behind it were fully open: anyone who reached
a service URL could list listings, drive workflows, and decide approvals. The only
access controls were header secrets on machine endpoints — `CRON_SECRET` on
`/internal/cron/*` and the Vapi webhook signature on `/webhooks/vapi`. Real brokerage
deploys need human operators to authenticate; the demo must stay zero-credential.

Two constraints from earlier decisions shaped the design:

- **Demo is zero-credential** (the standing rule behind ADR-0006's deterministic
  default): `docker compose up` and the public demo deploy must run with no secrets
  and no login friction.
- **One frontend image serves every client** (ADR-0003): the SPA bakes `VITE_API_BASE=
  /api` once. Anything client-specific must arrive at runtime, not at image build.

## Decision

Authenticate operators with **Google OIDC, verifying the ID token directly**, behind a
port/adapter seam that mirrors ADR-0006:

1. `IdentityVerifier` (`core/ports/identity.py`) — `async verify(token) -> Principal`.
   `core/` depends only on this Protocol; `Principal` (subject/email/name) is the
   contract. `AuthError` carries a `forbidden` flag so the API maps "untrusted token"
   to 401 and "valid identity, not allowed" to 403.
2. `DemoIdentityVerifier` (core) is the **default adapter**: it returns a fixed demo
   operator regardless of the token. This is what keeps demo mode login-free — every
   protected route resolves a principal with no bearer present.
3. `GoogleOIDCVerifier` (`integrations/google_oidc/`) verifies the token against
   Google's certs (audience = our OAuth client id, issuer + verified-email checks),
   then applies a flat allowlist (Workspace domain and/or explicit emails).
4. **Selection is wiring-time**: `build_identity_verifier` returns the Google adapter
   only when `GOOGLE_OIDC_CLIENT_ID` is set (and not the Terraform `"unset"`
   placeholder); otherwise the demo default. Auth is applied uniformly by attaching the
   `get_current_principal` dependency at router-include time to the operator routers;
   webhooks, cron, and `/auth/*` keep their own controls.
5. **The client id is served at runtime, not baked**: a public `GET /auth/config`
   returns `{enabled, client_id}` and the SPA bootstraps from it. OAuth client ids are
   public by design, so this is a plain env var (not a Secret Manager secret) and the
   single-frontend-image property (ADR-0003) is preserved.
6. **`decided_by` is stamped server-side** from the authenticated principal. The decide
   endpoint no longer trusts a client-supplied value, so the approval audit trail
   reflects a real identity.

### Why direct OIDC verification, not IAP

Identity-Aware Proxy would offload login entirely, but it requires an external HTTPS
load balancer in front of Cloud Run — infrastructure the per-client single-container
deploy doesn't have — and it makes the demo path (no LB, open) behave nothing like
prod. Direct verification keeps the deploy shape unchanged and makes "auto demo user
when nothing is configured" a one-line default. The `IdentityVerifier` seam means an
IAP/header-assertion adapter can drop in later without touching any caller.

### Why a direct adapter, not an MCP server (consistent with ADR-0006)

Identity verification is a request-time check at the API edge, not a workflow tool
surface. Like extraction, it rides a direct SDK (`google-auth`) inside an integration
package; the MCP servers remain for systems that workflows invoke as tools.

## Consequences

- (+) Demo stays zero-credential and login-free; the deterministic-style demo verifier
  exercises the protected paths in CI with no secrets.
- (+) One frontend image still serves every client — the client id is runtime config.
- (+) Approvals now record a verified operator identity instead of a client string.
- (+) Authorization is a flat allowlist today; the `Principal` shape leaves room for
  roles/RBAC later without re-plumbing the seam.
- (−) ID tokens (~1h) are used directly with no refresh flow — on expiry the SPA
  re-prompts. Acceptable for an operator console; revisit if long sessions are needed.
- (−) A new external dependency surface (`google-auth`, already in the lockfile) and a
  Google OAuth client to provision per real client.
