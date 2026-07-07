// Where the API lives: same-origin `/api` behind nginx in the cloud (ADR-0003),
// the direct localhost URL in dev.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// The access token (Google ID token or session JWT) lives in memory +
// sessionStorage so a reload doesn't force a re-login, but it never persists
// past the browser session. The magic-link flow also issues a refresh token
// (stored the same way) that apiFetch exchanges for a new access token when the
// short-lived access token expires, so an operator isn't bounced mid-session
// (ADR-0013). Google has no refresh token — that path just re-prompts on expiry.
const TOKEN_KEY = "brokerops_id_token";
const REFRESH_KEY = "brokerops_refresh_token";

let token: string | null = sessionStorage.getItem(TOKEN_KEY);
let refreshToken: string | null = sessionStorage.getItem(REFRESH_KEY);
let onUnauthorized: (() => void) | null = null;

// Bumped on every login (setSession) and sign-out/dead-session (clearSession). An
// in-flight refresh captures the generation it started under; if a sign-out or a
// different login lands before it resolves, the generation no longer matches and
// the stale refresh is discarded — it must never resurrect a signed-out session or
// clobber a newer login.
let sessionGeneration = 0;

export function getToken(): string | null {
  return token;
}

export function setToken(value: string | null): void {
  token = value;
  if (value) sessionStorage.setItem(TOKEN_KEY, value);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function setRefreshToken(value: string | null): void {
  refreshToken = value;
  if (value) sessionStorage.setItem(REFRESH_KEY, value);
  else sessionStorage.removeItem(REFRESH_KEY);
}

// Store both credentials after a login; clear both on sign-out or a dead session.
// Both bump the session generation so any refresh in flight for the prior session
// is invalidated.
export function setSession(access: string, refresh: string): void {
  setToken(access);
  setRefreshToken(refresh);
  sessionGeneration += 1;
}

export function clearSession(): void {
  setToken(null);
  setRefreshToken(null);
  // Drop any pending post-login redirect too, so a signed-out (or dead) session
  // can't leave a deep link behind for the next sign-in on this browser (BOP-025).
  // On the mid-session-401 path apiFetch clears here and the unauthorized handler
  // immediately re-saves the *current* path, so the live deep link is preserved.
  localStorage.removeItem(REDIRECT_KEY);
  sessionGeneration += 1;
}

// Google logins have no refresh token: store the ID token as the access bearer and
// clear any refresh token left from a prior magic session. Bumping the generation
// invalidates a magic refresh that may still be in flight, so it can't clobber this
// login or leave a usable refresh token on the Google path.
export function setAccessOnlySession(access: string): void {
  setToken(access);
  setRefreshToken(null);
  sessionGeneration += 1;
}

// The AuthProvider registers a handler so a 401 anywhere bounces the operator
// back to the sign-in screen instead of failing silently.
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

// Deep-link preservation across the login round-trip (BOP-025). When an
// unauthenticated visit lands on a deep link (e.g. an approval permalink from a
// notification email), the intended path is stashed here before the sign-in
// screen shows; the index route consumes it after login so the operator returns
// to where they were headed.
//
// This uses localStorage, not sessionStorage: the magic-link flow re-enters the
// app on a SEPARATE browsing context — the email link commonly opens in a new
// tab — and sessionStorage is per-tab, so it would be gone by the callback. Only
// localStorage (shared across tabs of the same origin) survives that hop. It is
// still same-browser only: opening the link in a different browser loses the
// stash and lands on the home route, an acceptable degradation.
const REDIRECT_KEY = "brokerops_post_login_redirect";

// A destination is only accepted if it is a same-origin app path: a single
// leading "/" (not "//" or "/\" which parse as protocol-relative → another
// origin), and not the auth/callback URLs (not destinations; the callback still
// carries the single-use magic token). This is the open-redirect guard for the
// value that later reaches the router as a raw href.
function isInternalPath(path: string): boolean {
  return (
    path.length > 1 &&
    path.startsWith("/") &&
    path[1] !== "/" &&
    path[1] !== "\\" &&
    !path.startsWith("/auth")
  );
}

export function savePostLoginRedirect(path: string): void {
  // Entering login from a real deep link stashes it; entering from a
  // non-destination (root, the callback, anything invalid) instead CLEARS any
  // prior stash — otherwise a redirect saved by an abandoned earlier login could
  // later be consumed by the next person to sign in on this browser and send them
  // to a stranger's entity permalink (localStorage outlives the tab).
  if (isInternalPath(path)) localStorage.setItem(REDIRECT_KEY, path);
  else localStorage.removeItem(REDIRECT_KEY);
}

export function takePostLoginRedirect(): string | null {
  const path = localStorage.getItem(REDIRECT_KEY);
  if (path == null) return null;
  localStorage.removeItem(REDIRECT_KEY);
  // Re-validate on read: the value is user-influenced (it came from the URL) and
  // is about to be handed to the router as an href, so never trust it blindly.
  return isInternalPath(path) ? path : null;
}

export type AuthConfig = { enabled: boolean; methods: string[]; client_id: string | null };

export async function loadAuthConfig(): Promise<AuthConfig> {
  const response = await fetch(`${API_BASE}/auth/config`);
  if (!response.ok) throw new Error(`auth config returned ${response.status}`);
  return (await response.json()) as AuthConfig;
}

// Pre-auth flows use plain fetch (no bearer to attach yet).
export async function requestMagicLink(email: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/magic/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (response.status === 429) {
    throw new Error("Too many requests — wait a minute and try again.");
  }
  if (!response.ok) throw new Error(`request failed (${response.status})`);
}

export type SessionTokens = { access: string; refresh: string };

export async function redeemMagicLink(token: string): Promise<SessionTokens> {
  const response = await fetch(`${API_BASE}/auth/magic/redeem`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) throw new Error("This sign-in link is invalid or has expired.");
  const data = (await response.json()) as { session_token: string; refresh_token: string };
  return { access: data.session_token, refresh: data.refresh_token };
}

// A single in-flight refresh shared across concurrent 401s, so a burst of requests
// that all expire together triggers exactly one /auth/refresh. It is keyed to the
// session generation it was started for: a caller from a newer session must NOT
// reuse a prior session's refresh (that stale promise is doomed to resolve false
// and would otherwise deny the newer session its own refresh).
let refreshInFlight: { generation: number; promise: Promise<boolean> } | null = null;

// Exchange the refresh token for a fresh access token. Returns false when there is
// no refresh token, the server rejects it, or the session changed mid-flight — the
// caller then treats the original 401 as terminal. Google logins have no refresh
// token, so this is a no-op there.
export function refreshSession(): Promise<boolean> {
  const generation = sessionGeneration;
  // Share an in-flight refresh only with callers of the SAME session generation.
  if (refreshInFlight && refreshInFlight.generation === generation) {
    return refreshInFlight.promise;
  }
  const current = refreshToken;
  if (!current) return Promise.resolve(false);
  const promise = (async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: current }),
      });
      // A sign-out or a different login landed while we were awaiting: the newer
      // state wins. Discard this result without touching the session. Teardown on a
      // genuine failure is left to apiFetch (also generation-gated), so this never
      // bumps the generation and can't mask a concurrent logout/login.
      if (sessionGeneration !== generation) return false;
      if (!response.ok) return false;
      const data = (await response.json()) as { session_token: string };
      // Re-check after the second await (parsing the body) for the same reason.
      if (sessionGeneration !== generation) return false;
      setToken(data.session_token); // same session — deliberately does not bump the generation
      return true;
    } catch {
      return false;
    } finally {
      // Only clear our own entry — a newer session may have replaced it meanwhile.
      if (refreshInFlight && refreshInFlight.generation === generation) {
        refreshInFlight = null;
      }
    }
  })();
  refreshInFlight = { generation, promise };
  return promise;
}

// Drop-in for fetch on protected endpoints: attaches the bearer when present. On
// a 401 it tries once to renew the access token via the refresh token and, if
// that succeeds, replays the request; otherwise it clears the session so the SPA
// re-prompts. Accepts a `Request` as well as a URL because it is also the
// generated API client's fetch layer (ADR-0018): the client builds a Request and
// hands it here, so every generated call inherits the bearer/refresh behavior.
export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const generation = sessionGeneration;
  const send = () => {
    if (input instanceof Request) {
      // Clone per attempt: a Request body is single-use, and the 401 path may
      // replay — the original must stay pristine for the second send.
      const request = new Request(input.clone(), init);
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return fetch(request);
    }
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(input, { ...init, headers });
  };
  let response = await send();
  // If a sign-out or a different login replaced the session while this request was
  // in flight, it belongs to a session that no longer exists: do NOT refresh or
  // replay it (that would run the old request under the new session's credentials),
  // and do NOT tear down the newer session. Just surface the original 401.
  if (response.status === 401 && sessionGeneration !== generation) {
    return response;
  }
  if (response.status === 401 && (await refreshSession())) {
    response = await send();
  }
  // Only tear down the session this request was acting for — re-check the
  // generation in case a login/logout landed during the refresh/replay.
  if (response.status === 401 && sessionGeneration === generation) {
    clearSession();
    onUnauthorized?.();
  }
  return response;
}
