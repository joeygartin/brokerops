import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import { roleAtLeast, type Role } from "./roles";
import {
  API_BASE,
  apiFetch,
  clearSession,
  loadAuthConfig,
  redeemMagicLink,
  requestMagicLink,
  savePostLoginRedirect,
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
  // The QueryClient lives above this provider (main.tsx) and outlives any single
  // session, so its cache of protected server state (listings, transactions,
  // approvals, audit, folder files) must be wiped whenever the session ends —
  // otherwise a later login on the same browser could render the prior
  // operator's cached data before it refetches (BOP-024/ADR-0022).
  const queryClient = useQueryClient();
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
    queryClient.clear();
    setEmail(null);
    setRole(null);
    window.google?.accounts.id.disableAutoSelect();
    setPhase("login");
  }, [queryClient]);

  // Bootstrap: read /auth/config, then either run open (demo), complete a magic
  // callback, resume a stored session, or show the sign-in screen.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      // A live session died mid-use (a 401 that couldn't be refreshed). Stash
      // where the operator was — a deep link they'll expect to return to — before
      // dropping them on the sign-in screen, so re-login restores it just like a
      // cold deep-link visit does (BOP-025). The URL still shows that path here.
      savePostLoginRedirect(window.location.pathname + window.location.search);
      queryClient.clear();
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
        // A stored token may still be valid after a reload — if so, the current
        // URL is already the destination (a deep link survives a reload), so
        // leave it untouched. Otherwise stash where the operator was headed so
        // the login round-trip can return them there (BOP-025).
        if (await loadMe()) setPhase("ready");
        else {
          savePostLoginRedirect(window.location.pathname + window.location.search);
          setPhase("login");
        }
      })
      .catch((cause) => setError(String(cause)));
    return () => setUnauthorizedHandler(null);
  }, [loadMe, queryClient]);

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
            loadMe().then((ok) => {
              // Scrub the sign-in URL to root; the index route restores the
              // saved deep link (BOP-025), matching the magic-link path.
              if (ok) window.history.replaceState({}, "", "/");
              setPhase(ok ? "ready" : "login");
            });
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
      <div className="grid min-h-screen place-items-center p-4">
        <div className="w-80 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">brokerops</h1>
          <p className="text-muted-foreground">Sign in to continue</p>

          {methods.includes("magic") &&
            (magicSent ? (
              <p className="rounded-md bg-success-soft p-3 text-success-soft-foreground">
                Check your email for a sign-in link.
              </p>
            ) : (
              <form onSubmit={submitMagic} className="grid gap-2">
                <Input
                  type="email"
                  required
                  placeholder="you@example.com"
                  value={magicEmail}
                  onChange={(e) => setMagicEmail(e.target.value)}
                />
                <Button type="submit" variant="success">
                  Email me a sign-in link
                </Button>
              </form>
            ))}

          {magicError && <p className="text-sm text-destructive">{magicError}</p>}

          {methods.includes("magic") && methods.includes("google") && (
            <div className="my-4 text-xs text-muted-foreground">or</div>
          )}

          {methods.includes("google") && (
            <div ref={buttonRef} className="mt-2 inline-block" />
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
    <div className="grid min-h-screen place-items-center p-4">
      <p className={tone === "error" ? "text-destructive" : "text-muted-foreground"}>{text}</p>
    </div>
  );
}
