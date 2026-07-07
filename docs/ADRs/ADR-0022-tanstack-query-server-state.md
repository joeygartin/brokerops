# ADR-0022: TanStack Query owns server state; no Redux/Zustand

**Status:** Accepted · **Date:** 2026-07-07

## Context

Each view fetched its own server state by hand: a `useState` holding the rows, a
`useEffect` firing the generated client, a `useCallback` `refresh()` re-run after
every mutation, and per-view `error` strings. The pattern worked but re-derived
caching, refetch-after-write, and loading/error handling in every file, and it had
no shared notion of "this data is stale, refetch it." The Approvals inbox in
particular wanted live-ish updates and had no polling.

The open question was whether to reach for a general client-state store
(Redux/Zustand) or a server-state cache. Almost all of the SPA's state is *server*
state — listings, transactions, approvals, the audit trail — whose source of truth
is the API, not the client. A general store would make us hand-roll caching and
invalidation on top of it; a server-state library gives those for free.

## Decision

1. **TanStack Query (`@tanstack/react-query`, v5) owns all server state.** A single
   `QueryClient` is created at the app root (`src/queryClient.ts`, mounted in
   `main.tsx` above the existing `AuthProvider`). Every read is a `useQuery`, every
   write a `useMutation`.

2. **No Redux/Zustand.** Server state lives in Query; local UI state (the active
   tab, an in-progress edit, a filter input) stays in React `useState`; the session
   bearer stays in the auth context (ADR-0013). We did not add a client-state store
   because there is almost no client state to hold — introducing one would mean
   re-deriving the caching/invalidation Query already provides.

3. **One hooks module per resource, over the generated client (ADR-0018).**
   `src/hooks/{listings,transactions,approvals,audit}.ts` wrap the generated SDK
   functions; views import hooks, never the SDK directly. Query keys live in one
   registry (`src/hooks/keys.ts`) so a mutation invalidates exactly the queries it
   affects — deciding an approval invalidates both the approvals inbox and the
   transactions board; starting a workflow or an outbound call invalidates the
   inbox; attaching a document or running the milestone cron invalidates the board.

4. **The Approvals inbox polls** (`refetchInterval` 7s, `refetchIntervalInBackground`
   false so it pauses while the tab is hidden). SSE was left out of scope — polling
   is enough until latency demonstrably hurts.

5. **The fetch layer is unchanged.** Query calls the generated client, which already
   routes through `apiFetch` (bearer header + single shared 401 refresh-and-replay,
   ADR-0013/0018). Retries are off for 4xx (a rejected request won't succeed on a
   replay) and capped for transient 5xx/network failures; `unwrap` now throws a
   status-carrying `ApiError` so the retry predicate can tell the two apart. The
   milestone cron trigger stays on a plain `fetch` inside its hook: it is gated by
   `X-Cron-Key`, not the bearer, and its 401 must not trip the session-teardown path.

6. **The cache is wiped on session teardown.** The `QueryClient` is mounted above
   `AuthProvider` and outlives any single session, so `AuthProvider` calls
   `queryClient.clear()` on sign-out and on the terminal-401 unauthorized handler.
   Before this, the pre-Query views held server data in component `useState` that
   unmounted with the app on sign-out; the persistent cache would otherwise let a
   later login on the same browser render the prior operator's cached listings /
   transactions / approvals / audit / folder files before the refetch landed. A
   regression test seeds the cache, drives sign-out, and asserts the data is gone.

## Consequences

- (+) Loading/error/empty states are first-class per view from Query's status flags,
  not hand-rolled; mutations refresh the right views by invalidation, deleting the
  bespoke `refresh()`/`onChanged` plumbing.
- (+) The inbox updates near-live without a bespoke poller.
- (−) Component tests render under a `QueryClientProvider` (a small
  `renderWithClient` test helper); this was a mechanical update and the auth suite
  was untouched.
- (−) One more runtime dependency and ~30 kB gzipped in the bundle — paid for by the
  per-view fetch/cache/invalidate boilerplate it removes.
