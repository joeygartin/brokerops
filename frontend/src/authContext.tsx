import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { API_BASE, roleAtLeast, type Role } from "./types";
import {
  apiFetch,
  clearSession,
  loadAuthConfig,
  redeemMagicLink,
  requestMagicLink,
  setAccessOnlySession,
  setSession,
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

type AuthState = {
  email: string | null;
  role: Role | null;
  // True if the current operator's role meets `minimum`. Hides controls the API
  // would reject anyway; the server remains the authority.
  hasRole: (minimum: Role) => boolean;
  signOut: () => void;
};

const AuthContext = createContext<AuthState>({
  email: null,
  role: null,
  hasRole: () => false,
  signOut: () => {},
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

type Phase = "loading" | "demo" | "login" | "ready";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [magicEmail, setMagicEmail] = useState("");
  const [magicSent, setMagicSent] = useState(false);
  const [magicError, setMagicError] = useState<string | null>(null);
  const buttonRef = useRef<HTMLDivElement>(null);

  // Confirm the current bearer and capture the operator's email.
  const loadMe = useCallback(async (): Promise<boolean> => {
    const response = await apiFetch(`${API_BASE}/auth/me`);
    if (!response.ok) return false;
    const me = (await response.json()) as { email: string; role: Role };
    setEmail(me.email);
    setRole(me.role);
    return true;
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setEmail(null);
    setRole(null);
    window.google?.accounts.id.disableAutoSelect();
    setPhase("login");
  }, []);

  // Bootstrap: read /auth/config, then either run open (demo), complete a magic
  // callback, resume a stored session, or show the sign-in screen.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setEmail(null);
      setRole(null);
      setPhase("login");
    });
    loadAuthConfig()
      .then(async (cfg) => {
        setConfig(cfg);
        if (!cfg.enabled) {
          setPhase("demo");
          return;
        }
        // Magic-link callback: redeem the URL token for a session, then scrub it.
        const token = new URL(window.location.href).searchParams.get("token");
        if (token) {
          try {
            const { access, refresh } = await redeemMagicLink(token);
            setSession(access, refresh);
            window.history.replaceState({}, "", "/");
            if (await loadMe()) {
              setPhase("ready");
              return;
            }
          } catch (cause) {
            setMagicError(String(cause));
          }
        }
        // A stored token may still be valid after a reload.
        if (await loadMe()) setPhase("ready");
        else setPhase("login");
      })
      .catch((cause) => setError(String(cause)));
    return () => setUnauthorizedHandler(null);
  }, [loadMe]);

  const submitMagic = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      setMagicError(null);
      try {
        await requestMagicLink(magicEmail.trim());
        setMagicSent(true);
      } catch (cause) {
        setMagicError(String(cause));
      }
    },
    [magicEmail],
  );

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
            setAccessOnlySession(response.credential);
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
    const methods = config?.methods ?? [];
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <div style={{ textAlign: "center", width: 320 }}>
          <h1>brokerops</h1>
          <p style={{ color: "#57606a" }}>Sign in to continue</p>

          {methods.includes("magic") &&
            (magicSent ? (
              <p
                style={{
                  background: "#dafbe1",
                  color: "#1a7f37",
                  borderRadius: 6,
                  padding: "0.75rem",
                }}
              >
                Check your email for a sign-in link.
              </p>
            ) : (
              <form onSubmit={submitMagic} style={{ display: "grid", gap: "0.5rem" }}>
                <input
                  type="email"
                  required
                  placeholder="you@example.com"
                  value={magicEmail}
                  onChange={(e) => setMagicEmail(e.target.value)}
                  style={{
                    padding: "0.5rem 0.7rem",
                    borderRadius: 6,
                    border: "1px solid #d0d7de",
                    fontSize: "0.95rem",
                  }}
                />
                <button
                  type="submit"
                  style={{
                    padding: "0.5rem 1.1rem",
                    borderRadius: 6,
                    border: "1px solid #1a7f37",
                    background: "#2da44e",
                    color: "#fff",
                    cursor: "pointer",
                  }}
                >
                  Email me a sign-in link
                </button>
              </form>
            ))}

          {magicError && (
            <p style={{ color: "#cf222e", fontSize: "0.85rem" }}>{magicError}</p>
          )}

          {methods.includes("magic") && methods.includes("google") && (
            <div style={{ color: "#57606a", margin: "1rem 0", fontSize: "0.8rem" }}>or</div>
          )}

          {methods.includes("google") && (
            <div ref={buttonRef} style={{ display: "inline-block", marginTop: "0.5rem" }} />
          )}
        </div>
      </div>
    );
  }
  // Demo mode (auth disabled) is full-access — it matches the backend's demo
  // operator (admin). With auth on, children only render once loadMe has set the
  // role, so there's no flash of hidden-then-shown controls.
  const effectiveRole: Role | null = config?.enabled ? role : "admin";
  const hasRole = (minimum: Role) => roleAtLeast(effectiveRole, minimum);
  return (
    <AuthContext.Provider value={{ email, role: effectiveRole, hasRole, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

function CenteredNote({ text, tone }: { text: string; tone: "error" | "muted" }) {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
      <p style={{ color: tone === "error" ? "#cf222e" : "#57606a" }}>{text}</p>
    </div>
  );
}
