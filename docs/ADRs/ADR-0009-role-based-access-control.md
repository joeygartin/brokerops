# ADR-0009: Role-based access control for operators

**Status:** Accepted · **Date:** 2026-06-21 · **Builds on:** ADR-0007, ADR-0008

## Context

ADR-0007/0008 shipped operator login (Google OIDC + magic link) gated by a shared
email **allowlist**. The allowlist answers *who may sign in* — but every signed-in
operator then has identical, full authority, including deciding human-in-the-loop
approvals (`POST /approvals/{id}/decide`). That decision is the one action with
real-world side effects: it resumes a workflow that writes to the CRM, advances a
contract, or triggers an outbound call. A real brokerage backoffice has staff who
should view and assist but not commit those decisions. We need *what they may do* to
be distinct from *whether they may sign in*, without adding a per-request database
lookup or breaking the zero-credential demo.

## Decision

Add a small, hierarchical role on the `Principal`, assigned from deployment config at
identity time — parallel to the allowlist, not a replacement for it.

1. **`Role` (core):** `viewer < operator < admin`, with `role.allows(minimum)`. viewer
   reads; operator also acts (start workflows, place calls); admin also decides
   approvals. `Principal` carries `role` (default `operator`).
2. **`RoleResolver` (core):** maps an already-allowlisted email to a role from config
   (`admin`/`viewer` emails + domains), mirroring `EmailAllowlist`. **Unrestricted by
   default:** with no admin or viewer rule set it grants `admin` to everyone, so
   enabling auth without role config behaves exactly as before RBAC. Once any rule is
   set, an unmatched email defaults to `operator`; admin rules win over viewer.
3. **Role at identity time, carried in the bearer.** The resolver runs in every verifier
   path — `DemoIdentityVerifier` (always `admin`, keeps the login-free demo whole),
   magic-link redeem (baked into the session JWT), and `GoogleOIDCVerifier` (resolved
   per request, since Google ID tokens are verified fresh). The session JWT gains a
   `role` claim; a token with no/invalid role claim resolves to `operator` so a stale
   bearer can never self-elevate. No new per-request DB lookup.
4. **`require_role(minimum)` (api):** a dependency factory gating the privilege-sensitive
   routes — `admin` on approval decisions, `operator` on workflow start and outbound
   calls. Reads stay open to any authenticated operator (viewer and up). Authorization
   runs after authentication, so a role failure is a 403, never a 401.
5. **Config:** `AUTH_ADMIN_EMAILS` / `AUTH_ADMIN_DOMAIN` / `AUTH_VIEWER_EMAILS` /
   `AUTH_VIEWER_DOMAIN`, with matching Terraform vars wired as Cloud Run env only when
   `enable_auth` is on. No secrets — roles are not sensitive.

## Consequences

- **Backward compatible.** An auth-enabled deploy with no role config is a flat admin
  list (today's behavior); the demo is unaffected (demo operator is admin).
- **Enforced server-side**, the security boundary. The frontend can later read the role
  from `/auth/me` to hide controls, but the API is authoritative.
- **Stateless**, so a role change takes effect immediately for Google logins and within
  the session-JWT TTL (≤8h) for magic-link logins — enabling RBAC asks existing
  magic-link sessions to re-login to pick up their role.
- **Flat roles, not granular permissions.** A three-level hierarchy covers the real
  split (read / act / decide); per-permission grants can come later behind the same
  `Principal` seam if a deployment needs them. No speculative abstraction now.
