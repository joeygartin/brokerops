import { API_BASE } from "./types";

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
// re-prompts.
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const generation = sessionGeneration;
  const send = () => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(url, { ...init, headers });
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
