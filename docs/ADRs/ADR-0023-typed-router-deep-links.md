# ADR-0023: TanStack Router — typed routes make every entity addressable

**Status:** Accepted · **Date:** 2026-07-07

## Context

The UI was four flat tabs held in a single `useState<Tab>`. Nothing had a URL:
a listing, a transaction, or an approval could not be linked to, a browser refresh
dropped the operator back to the default tab, and — the immediate blocker — a
notification email cannot say "click here to approve" without a stable, addressable
URL for one approval card. URL-addressability is a product requirement, not a
nicety: the next work (BOP-019 notification links) consumes `/approvals/:id`.

The choice was a router library versus hand-rolling `history.pushState`. We already
run TanStack Query (ADR-0022); TanStack Router is the same family, fully typed, and
integrates with the generated-client/typed-params story (ADR-0018) without a bespoke
matcher.

## Decision

1. **TanStack Router (`@tanstack/react-router`, v1), code-based route tree.** The
   four tabs became top-level routes and every entity got a detail route:
   `/listings` + `/listings/$key`, `/transactions` + `/transactions/$id`,
   `/approvals` + `/approvals/$id`, `/audit`. `/` redirects (see §4). Active nav
   state derives from the URL (`<Link activeProps>`), not component state. We use the
   **code-based** tree (`createRoute` in `src/router.tsx`), not the file-based
   codegen plugin — no build step, and the tree is small enough to read in one file.

2. **Detail routes reuse the board cards but fetch through the keyed read
   endpoint.** Each entity has a per-item endpoint — `GET /listings/{key}`,
   `GET /transactions/{id}`, `GET /approvals/{id}` — so the detail views resolve the
   routed key/id through those (`useListing` / `useTransaction` / `useApproval`),
   **not** a `find()` over the board query. That matters for addressability: the
   listings board is a default-search subset and the transactions board is
   active-only, so a `find()` would render a *false* not-found for a real entity
   simply absent from the list. The keyed read returns the entity at any status; a
   genuinely unknown id 404s into a clean not-found state (an `ApiError` the view
   distinguishes from a transient failure), never a crash. For approvals the keyed
   read returns the row at any status, so the view tells a still-pending approval
   (render the card) from one already decided (a clean "no longer pending" state) —
   the case an emailed `/approvals/:id` link hits *after* someone else has acted,
   the common case, not an edge. The detail query key nests under the collection key
   so a mutation invalidating the collection also refreshes an open permalink.

3. **No route-level page lock today — role access mirrors `require_role` at the
   control level.** `require_role` on the API gates only *writes*; every GET is
   viewer-open, and ADR-0009 deliberately keeps **all tabs visible to all roles**,
   hiding only the controls a role can't use. So there is no operator-only *read* to
   mirror, and locking any current route (even the audit ledger) would be a
   role-gating regression against ADR-0009 — the API, not the UI, is the authority,
   and the UI must not be *looser* nor gratuitously *stricter* than it on reads. The
   faithful mirror of `require_role`'s write gates therefore lives where the writes
   are: gated in-card by `hasRole` (approve = admin, start-workflow / outbound-call =
   operator), unchanged by this task and covered by the board specs. A route-level
   guard primitive was prototyped and **removed** as premature (code-minimalism: it
   locked no real route). It belongs with BOP-030's genuinely role-shaped surfaces —
   built then, alongside any server-side read gate that would make a route lock a
   *true* mirror rather than a UI-only divergence.

4. **Login preserves the intended destination.** An unauthenticated visit to a deep
   link stashes the path (`savePostLoginRedirect`) before the sign-in screen shows;
   after login the `/` index route consumes it (`takePostLoginRedirect`) and
   redirects there, falling back to `/listings`. Both login methods scrub their
   sign-in URL to `/` so the restore path is uniform; a reload with a live session
   needs nothing (the deep link is already the URL). The stash uses **localStorage,
   not sessionStorage**: the magic-link flow re-enters the app on a separate browsing
   context — the email link commonly opens in a *new tab* — and sessionStorage is
   per-tab, so it would be gone by the callback; only localStorage (shared across
   tabs of one origin) survives that hop. Still same-browser only — opening the link
   in a different browser loses the stash and lands on the home route, an acceptable
   degradation. The stored value is user-influenced (it came from the URL) and is
   handed to the router as a raw `href`, so it is validated as an internal path — a
   single leading `/`, not `//`- or `/\`-prefixed (which parse as another origin),
   not `/auth*` — on **both** save and read, closing an open-redirect.

5. **The router is created per-mount, after the auth bootstrap.** `App` creates the
   router in `useState`, not at module load, so its browser history reads the URL
   *after* `AuthProvider` has scrubbed any magic-link token from it — otherwise the
   router would capture the pre-scrub `/auth/callback?token=…` location.

6. **SPA fallback was already in place.** The production nginx image already serves
   `try_files $uri $uri/ /index.html` (ADR-0003), and the Vite dev server does SPA
   fallback by default, so deep links resolve on both the compose stack and the
   deployed container. Verified against the production bundle: `/approvals/:id` and
   `/transactions/:id` return `200` + `index.html`. No proxy change.

## Consequences

- (+) Every entity is addressable: boards render an "Open ↗" permalink into each
  detail route, refresh keeps context, and `/approvals/:id` is ready for the
  notification-link work (BOP-019).
- (+) Login returns the operator to the deep link they were headed for — whether a
  cold visit or a mid-session expiry — instead of dropping them on the home board.
- (−) Views that render a routed `<Link>` need a router in tests — a small
  `renderRouted` helper alongside the router-free `renderWithClient` (ADR-0022);
  which one a spec uses depends only on whether the component links.
- (−) One more runtime dependency in the bundle, paid for by deleting the tab
  `useState` and gaining URL state and deep links.
- (~) Route-level role guards were explicitly *not* shipped (see Decision §3); the
  role mirror stays at the control level until BOP-030 adds role-shaped surfaces.
