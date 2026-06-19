import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { API_BASE } from "./types";
import {
  apiFetch,
  loadAuthConfig,
  setToken,
  setUnauthorizedHandler,
  type AuthConfig,
} from "./auth";

// Minimal shape of the Google Identity Services client we use (loaded from a
// script tag, so no @types package). Only the calls we make are declared.
type GoogleCredentialResponse = { credential: string };
type GoogleIdApi = {
  initialize: (config: { client_id: string; callback: (r: GoogleCredentialResponse) => void }) => void;
  renderButton: (parent: HTMLElement, options: { theme: string; size: string }) => void;
  disableAutoSelect: () => void;
};
declare global {
  interface Window {
    google?: { accounts: { id: GoogleIdApi } };
  }
}

const GIS_SRC = "https://accounts.google.com/gsi/client";

function loadGisScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) return resolve();
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${GIS_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("failed to load Google sign-in")));
      return;
    }
    const script = document.createElement("script");
    script.src = GIS_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("failed to load Google sign-in"));
    document.head.appendChild(script);
  });
}

type AuthState = { email: string | null; signOut: () => void };

const AuthContext = createContext<AuthState>({ email: null, signOut: () => {} });

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

type Phase = "loading" | "demo" | "login" | "ready";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const buttonRef = useRef<HTMLDivElement>(null);

  // Confirm the current bearer and capture the operator's email.
  const loadMe = useCallback(async (): Promise<boolean> => {
    const response = await apiFetch(`${API_BASE}/auth/me`);
    if (!response.ok) return false;
    const me = (await response.json()) as { email: string };
    setEmail(me.email);
    return true;
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setEmail(null);
    window.google?.accounts.id.disableAutoSelect();
    setPhase("login");
  }, []);

  // Bootstrap: read /auth/config, then either run open (demo) or resume/await a
  // Google sign-in.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setEmail(null);
      setPhase("login");
    });
    loadAuthConfig()
      .then(async (cfg) => {
        setConfig(cfg);
        if (!cfg.enabled) {
          setPhase("demo");
          return;
        }
        // A stored token may still be valid after a reload.
        if (await loadMe()) setPhase("ready");
        else setPhase("login");
      })
      .catch((cause) => setError(String(cause)));
    return () => setUnauthorizedHandler(null);
  }, [loadMe]);

  // Render the Google button once we're in the login phase and have a client id.
  useEffect(() => {
    if (phase !== "login" || !config?.client_id) return;
    let cancelled = false;
    loadGisScript()
      .then(() => {
        if (cancelled || !buttonRef.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: config.client_id!,
          callback: (response) => {
            setToken(response.credential);
            loadMe().then((ok) => setPhase(ok ? "ready" : "login"));
          },
        });
        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: "outline",
          size: "large",
        });
      })
      .catch((cause) => setError(String(cause)));
    return () => {
      cancelled = true;
    };
  }, [phase, config, loadMe]);

  if (error) {
    return <CenteredNote text={`Sign-in unavailable: ${error}`} tone="error" />;
  }
  if (phase === "loading") {
    return <CenteredNote text="Loading…" tone="muted" />;
  }
  if (phase === "login") {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", gap: "1rem" }}>
        <div style={{ textAlign: "center" }}>
          <h1>brokerops</h1>
          <p style={{ color: "#57606a" }}>Sign in to continue</p>
          <div ref={buttonRef} style={{ display: "inline-block", marginTop: "0.5rem" }} />
        </div>
      </div>
    );
  }
  return <AuthContext.Provider value={{ email, signOut }}>{children}</AuthContext.Provider>;
}

function CenteredNote({ text, tone }: { text: string; tone: "error" | "muted" }) {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
      <p style={{ color: tone === "error" ? "#cf222e" : "#57606a" }}>{text}</p>
    </div>
  );
}
