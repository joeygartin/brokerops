# ADR-0008: Multi-method operator auth — magic link alongside Google OIDC

**Status:** Accepted · **Date:** 2026-06-19 · **Builds on:** ADR-0003, ADR-0007

## Context

ADR-0007 shipped operator login via Google OIDC behind an `IdentityVerifier` port.
That quietly assumes every operator has a Google account — false for many brokerage
staff (Outlook, a brokerage domain, etc.). We want a deployment to offer **magic-link
email login**, **Google OIDC**, or **both**, gated by the same email allowlist, while
demo mode stays zero-credential and login-free.

Google OIDC is *stateless*: the bearer the browser sends **is** the proof, so
`verify(token) -> Principal` is the whole story. Magic link is different — it
authenticates **once** (open the emailed link), after which the browser needs a
durable credential to stay signed in. That introduces three things OIDC didn't need:
a login *initiation* step, a self-issued *session* credential, and an *email* channel.

## Decision

Generalize the seam so login *methods* and bearer *verification* are separate, and a
**self-issued session JWT** is the unifying bearer.

1. **Session JWT as the common bearer.** Magic-link redemption issues a short-lived
   HS256 JWT (`SessionTokenService`, api layer, `iss=brokerops`). `SessionTokenVerifier`
   implements the core `IdentityVerifier` Protocol, so a session JWT and a Google ID
   token are interchangeable bearers — `get_current_principal` is unchanged.
2. **CompositeIdentityVerifier.** `build_identity_verifier` assembles one verifier per
   enabled method and tries each; first to accept wins, all-fail → 401, any forbidden
   → 403. No methods configured → `DemoIdentityVerifier` (the ADR-0007 default).
3. **Methods are config.** `AUTH_METHODS` (csv of `google`/`magic`) drives it; a bare
   `GOOGLE_OIDC_CLIENT_ID` still enables google for back-compat. `/auth/config`
   advertises the enabled methods so the SPA renders the right UI (email box and/or
   Google button) — still served at runtime, preserving one frontend image (ADR-0003).
4. **Magic-link flow (core `MagicLinkService`, pure logic over Protocols).**
   `request(email)`: allowlist-gate → high-entropy token (`secrets.token_urlsafe`) →
   store only its SHA-256 hash with a 15-min expiry → email a link. `redeem(token)`:
   atomic single-use consume → expiry + allowlist re-check → issue a session JWT.
5. **Email behind a port.** `EmailSender` (core) with `ConsoleEmailSender` as the
   zero-credential default (logs the link — magic link works in demo/local with no
   provider) and `SMTPEmailSender` (`integrations/email_smtp`, stdlib `smtplib`, any
   provider) when `SMTP_HOST` is set. Same pattern as ADR-0006's extractor default.
6. **Allowlist shared.** The `EmailAllowlist` (domain + emails) extracted from the
   Google verifier is reused by magic link, so a deployment's access list lives in one
   place and is enforced identically — at token time for Google, at request **and**
   redeem time for magic.

### Security properties (enforced in `MagicLinkService` / the route)

High-entropy single-use tokens; only the SHA-256 hash is persisted; consume is an
atomic `UPDATE … WHERE consumed_at IS NULL RETURNING` (single-use survives races);
15-min TTL; allowlist checked at request and redeem; `/auth/magic/request` always
returns 202 (no email enumeration) and silently drops non-allowlisted addresses; a
coarse per-email request throttle; session JWT short TTL (8h) with re-login on expiry.

### Why an explicit `public_base_url` (not derived)

Magic-link emails need the API to know the public **frontend** URL. But the frontend
already depends on the API's `.uri` at deploy (ADR-0003), so auto-deriving the reverse
would create a Terraform cycle. `public_base_url` is therefore an explicit config
string — a custom domain, or the frontend run.app URL supplied on a second apply —
never a resource reference. The session signing key, by contrast, is terraform-
generated (`random_password`, like the cron secret), so enabling magic link needs no
manual key handling; only the SMTP password is a pushed secret.

### Why session bearer, not an httpOnly cookie

A bearer keeps the post-login credential uniform with the Google path (both flow
through `Authorization: Bearer` and one `verify()`), avoiding a parallel cookie/CSRF
surface. A cookie would be marginally stronger against XSS token theft; revisit if the
threat model warrants it.

## Provisioning a Google OAuth client (runbook)

There is no clean `gcloud` path for a Web OAuth client id (the `iap oauth-clients`
commands are IAP-specific). In the Cloud Console for the target project: create an
OAuth **Web application** client; set **Authorized JavaScript origins** to the
frontend URL (the demo's is `https://brokerops-demo-frontend-fhjkz2f2hq-uw.a.run.app`);
no client secret is needed (GIS issues tokens in the browser). Put the client id in the
deploy's tfvars, set `enable_auth = true` and `auth_methods` (e.g. `"google,magic"`),
then `make deploy`. The client id is public, so it is a plain env var, not a secret.

## Consequences

- (+) Operators without Google accounts can sign in; deployments mix methods freely.
- (+) `get_current_principal`, the router-level auth, and the frontend bearer model are
  unchanged — magic link slots into the existing seam.
- (+) Demo stays zero-credential and login-free; the console email sender exercises the
  magic flow in dev with no provider.
- (+) Both engines are unaffected (auth is API-layer); the e2e demo gate is unchanged.
- (−) New stateful surface: a `magic_login_tokens` table, a session signing key, and an
  email dependency when SMTP is configured.
- (−) No refresh flow — session JWTs expire and re-prompt. Acceptable for an operator
  console; revisit for long-lived sessions. Authorization is still a flat allowlist; the
  `Principal` seam leaves room for roles/RBAC later.
