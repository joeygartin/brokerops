import { API_BASE } from "./types";

// The Google ID token lives in memory + sessionStorage so a reload doesn't
// force a re-login, but it never persists past the browser session.
const TOKEN_KEY = "brokerops_id_token";

let token: string | null = sessionStorage.getItem(TOKEN_KEY);
let onUnauthorized: (() => void) | null = null;

export function getToken(): string | null {
  return token;
}

export function setToken(value: string | null): void {
  token = value;
  if (value) sessionStorage.setItem(TOKEN_KEY, value);
  else sessionStorage.removeItem(TOKEN_KEY);
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

export async function redeemMagicLink(token: string): Promise<string> {
  const response = await fetch(`${API_BASE}/auth/magic/redeem`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) throw new Error("This sign-in link is invalid or has expired.");
  const data = (await response.json()) as { session_token: string };
  return data.session_token;
}

// Drop-in for fetch on protected endpoints: attaches the bearer when present
// and clears the session on 401 so the SPA re-prompts.
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    setToken(null);
    onUnauthorized?.();
  }
  return response;
}
