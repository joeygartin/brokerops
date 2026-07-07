import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "./authContext";
import { setToken } from "./auth";
import { queryKeys } from "./hooks/keys";
import type { Role } from "./roles";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Routes the two endpoints AuthProvider touches during bootstrap.
function routeFetch(config: unknown, me?: { email: string; role: Role } | number) {
  fetchMock.mockImplementation((url: string) => {
    if (url.endsWith("/auth/config")) return Promise.resolve(jsonResponse(config));
    if (url.endsWith("/auth/me")) {
      if (typeof me === "number") return Promise.resolve(new Response(null, { status: me }));
      if (me) return Promise.resolve(jsonResponse(me));
      return Promise.resolve(new Response(null, { status: 401 }));
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`));
  });
}

// Surfaces the context so specs can assert on role + hasRole gates.
function RoleProbe() {
  const { email, role, hasRole } = useAuth();
  return (
    <dl>
      <dd data-testid="email">{email ?? "—"}</dd>
      <dd data-testid="role">{role ?? "—"}</dd>
      <dd data-testid="viewer">{String(hasRole("viewer"))}</dd>
      <dd data-testid="operator">{String(hasRole("operator"))}</dd>
      <dd data-testid="admin">{String(hasRole("admin"))}</dd>
    </dl>
  );
}

function renderProvider() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <RoleProbe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setToken(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthProvider", () => {
  it("demo mode (auth disabled) grants full admin-equivalent access", async () => {
    routeFetch({ enabled: false, methods: [], client_id: null });
    renderProvider();

    // Children render once bootstrap resolves; role is the synthetic admin.
    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("admin"));
    expect(screen.getByTestId("admin")).toHaveTextContent("true");
    expect(screen.getByTestId("operator")).toHaveTextContent("true");
    expect(screen.getByTestId("viewer")).toHaveTextContent("true");
    // No /auth/me call in demo mode — only /auth/config.
    expect(fetchMock.mock.calls.every(([url]) => !String(url).endsWith("/auth/me"))).toBe(true);
  });

  it("auth on: a viewer session gates operator/admin controls off", async () => {
    setToken("session.jwt");
    routeFetch({ enabled: true, methods: ["magic"], client_id: null }, {
      email: "v@example.com",
      role: "viewer",
    });
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("email")).toHaveTextContent("v@example.com"));
    expect(screen.getByTestId("role")).toHaveTextContent("viewer");
    expect(screen.getByTestId("viewer")).toHaveTextContent("true");
    expect(screen.getByTestId("operator")).toHaveTextContent("false");
    expect(screen.getByTestId("admin")).toHaveTextContent("false");
  });

  it("auth on: an admin session clears every gate", async () => {
    setToken("session.jwt");
    routeFetch({ enabled: true, methods: ["magic"], client_id: null }, {
      email: "a@example.com",
      role: "admin",
    });
    renderProvider();

    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("admin"));
    expect(screen.getByTestId("operator")).toHaveTextContent("true");
    expect(screen.getByTestId("admin")).toHaveTextContent("true");
  });

  it("auth on with no valid session shows the sign-in screen, not the children", async () => {
    routeFetch({ enabled: true, methods: ["magic"], client_id: null }, 401);
    renderProvider();

    await waitFor(() => expect(screen.getByText("Sign in to continue")).toBeInTheDocument());
    // The gated children never mounted.
    expect(screen.queryByTestId("role")).not.toBeInTheDocument();
  });

  // Regression (BOP-024/ADR-0022): the QueryClient outlives a session, so signing
  // out must wipe cached server state — otherwise the next login on the same
  // browser could render the prior operator's data before it refetches.
  it("clears cached server state on sign-out so a later login can't see it", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Seed the cache as the prior (admin) session would have.
    client.setQueryData(queryKeys.approvals, [{ id: "prior-session-approval" }]);

    setToken("session.jwt");
    routeFetch({ enabled: true, methods: ["magic"], client_id: null }, {
      email: "a@example.com",
      role: "admin",
    });

    function SignOutProbe() {
      const { role, signOut } = useAuth();
      return (
        <div>
          <span data-testid="role">{role ?? "—"}</span>
          <button onClick={signOut}>Sign out</button>
        </div>
      );
    }

    render(
      <QueryClientProvider client={client}>
        <AuthProvider>
          <SignOutProbe />
        </AuthProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("role")).toHaveTextContent("admin"));
    expect(client.getQueryData(queryKeys.approvals)).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(client.getQueryData(queryKeys.approvals)).toBeUndefined());
  });
});
