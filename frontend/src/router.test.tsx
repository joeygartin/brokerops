import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Role } from "./roles";

// The routed shell (BOP-025). useAuth is mocked so specs drive the role; ./auth
// is mocked so the generated client's fetch layer and the post-login redirect
// hook are controllable (mirrors the board specs' approach).
const authState = vi.hoisted(() => ({ role: "admin" as Role }));
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
vi.mock("./authContext", () => ({
  useAuth: () => ({
    email: "op@example.com",
    role: authState.role,
    signOut: () => {},
    hasRole: (min: Role) => RANK[authState.role] >= RANK[min],
  }),
}));

const apiFetchMock = vi.hoisted(() => vi.fn());
const takeRedirectMock = vi.hoisted(() => vi.fn<() => string | null>());
vi.mock("./auth", () => ({
  apiFetch: apiFetchMock,
  API_BASE: "http://localhost:8000",
  takePostLoginRedirect: takeRedirectMock,
  savePostLoginRedirect: vi.fn(),
}));

import { routeTree } from "./router";

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

// Detail routes fetch a single entity via the keyed GET (/listings/{key} etc.),
// not the board list. The default mock 404s those keyed reads (so an unmapped id
// renders the clean not-found state) and returns an empty collection for the list
// endpoints; specs that need a specific entity override with fixtureFetch below.
const DETAIL_PATH = /^\/(listings|transactions|approvals)\/[^/]+$/;

function defaultFetch(request: Request): Response {
  const path = new URL(request.url).pathname;
  if (DETAIL_PATH.test(path)) return new Response(null, { status: 404 });
  return jsonOk([]);
}

function fixtureFetch(fixtures: Record<string, unknown>) {
  return (request: Request): Response => {
    const path = new URL(request.url).pathname;
    if (path in fixtures) return jsonOk(fixtures[path]);
    return defaultFetch(request);
  };
}

function renderAt(pathname: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [pathname] }),
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  authState.role = "admin";
  apiFetchMock.mockReset();
  // Collections come back empty; keyed detail reads 404 unless a spec supplies a
  // fixture. The generated client hands apiFetch a Request, so match on its URL.
  apiFetchMock.mockImplementation((request: Request) =>
    Promise.resolve(defaultFetch(request)),
  );
  takeRedirectMock.mockReset();
  takeRedirectMock.mockReturnValue(null);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("router", () => {
  it("renders the listings board and derives active nav state from the URL", async () => {
    renderAt("/listings");

    expect(await screen.findByText("No listings found.")).toBeInTheDocument();
    const listingsLink = screen.getByRole("link", { name: "Listings" });
    const transactionsLink = screen.getByRole("link", { name: "Transactions" });
    expect(listingsLink).toHaveAttribute("data-status", "active");
    expect(transactionsLink).not.toHaveAttribute("data-status", "active");
  });

  it("keeps the parent tab active on a detail route", async () => {
    renderAt("/transactions/T-404");

    // Unknown id → clean not-found state, and the Transactions tab stays active
    // (fuzzy match) so the user keeps their bearings.
    expect(await screen.findByText(/No transaction found for T-404/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Transactions" })).toHaveAttribute(
      "data-status",
      "active",
    );
  });

  it("shows a clean state for an unknown approval id, not a crash", async () => {
    renderAt("/approvals/does-not-exist");

    expect(await screen.findByText("This approval is no longer pending.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All approvals/ })).toBeInTheDocument();
  });

  it("404s an unknown path with a friendly page", async () => {
    renderAt("/nope");

    expect(await screen.findByText("Page not found.")).toBeInTheDocument();
  });

  // Regression (review r3): a deep link must resolve any real entity via its keyed
  // read, even one absent from the board list (listings are a default-search
  // subset; the transactions board is active-only). The board (list) endpoint
  // returns [] here, yet the keyed GET supplies the entity — the detail renders.
  it("resolves a listing deep link via the keyed read though it's not in the board list", async () => {
    apiFetchMock.mockImplementation((request: Request) =>
      Promise.resolve(
        fixtureFetch({
          "/listings/MLS-900": {
            mls_id: "MLS-900",
            status: "closed",
            address: "9 Hidden Way",
            city: "Redding",
            state: "CA",
            postal_code: "96001",
            list_price: 675000,
            bedrooms: 4,
            bathrooms: 3,
            living_area_sqft: 2400,
            year_built: 2004,
            agent_id: "a9",
            agent_name: "Sky Agent",
            remarks: "",
            modified_at: "2026-07-01T00:00:00Z",
            media: [],
          },
        })(request),
      ),
    );
    renderAt("/listings/MLS-900");

    expect(await screen.findByText("9 Hidden Way")).toBeInTheDocument();
    expect(screen.queryByText(/No listing found/)).not.toBeInTheDocument();
  });

  it("resolves a non-active transaction deep link via the keyed read", async () => {
    apiFetchMock.mockImplementation((request: Request) =>
      Promise.resolve(
        fixtureFetch({
          "/transactions/TXN-CLOSED": {
            transaction: {
              id: "TXN-CLOSED",
              listing_key: "MLS-900",
              stage: "closed",
              parties: [],
              close_date: "2026-06-01",
            },
            milestones: [],
            documents: [],
          },
        })(request),
      ),
    );
    renderAt("/transactions/TXN-CLOSED");

    expect(await screen.findByText(/TXN-CLOSED/)).toBeInTheDocument();
    expect(screen.queryByText(/No transaction found/)).not.toBeInTheDocument();
  });

  it("shows the clean 'no longer pending' state for a decided approval", async () => {
    apiFetchMock.mockImplementation((request: Request) =>
      Promise.resolve(
        fixtureFetch({
          "/approvals/APR-DONE": {
            id: "APR-DONE",
            workflow: "listing_to_contract",
            graph_thread_id: "thread-0001",
            kind: "approve_marketing",
            payload: { kind: "approve_marketing", listing_key: "MLS-900" },
            status: "approved",
            decided_by: "admin@example.com",
            created_at: "2026-07-01T00:00:00Z",
            decided_at: "2026-07-02T00:00:00Z",
          },
        })(request),
      ),
    );
    renderAt("/approvals/APR-DONE");

    // Found but decided → clean state, not the pending card.
    expect(await screen.findByText("This approval is no longer pending.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  describe("index redirect", () => {
    it("restores the saved deep link after login (over the role home)", async () => {
      takeRedirectMock.mockReturnValue("/approvals/APR-7");
      const router = renderAt("/");

      await waitFor(() => expect(router.state.location.pathname).toBe("/approvals/APR-7"));
      expect(takeRedirectMock).toHaveBeenCalled();
    });

    // BOP-030: with no saved deep link, "/" lands each role on their work.
    it("lands an admin on the approval inbox", async () => {
      authState.role = "admin";
      const router = renderAt("/");

      await waitFor(() => expect(router.state.location.pathname).toBe("/approvals"));
    });

    it("lands an operator on the deadline queue", async () => {
      authState.role = "operator";
      const router = renderAt("/");

      await waitFor(() => expect(router.state.location.pathname).toBe("/deadlines"));
    });

    it("lands a viewer on search", async () => {
      authState.role = "viewer";
      const router = renderAt("/");

      await waitFor(() => expect(router.state.location.pathname).toBe("/search"));
    });
  });

  // Role access is a UI mirror of require_role, which gates only writes — every
  // read route is viewer-open (ADR-0009). So a viewer reaches every view; the
  // role mirror lives at the control level (gated in-card, covered by the board
  // specs), not as a route-level page lock. These assert the "reads stay open"
  // half — a viewer is never denied a read route, not even the audit ledger.
  it("lets a viewer reach the audit ledger (no route-level lock)", async () => {
    authState.role = "viewer";
    renderAt("/audit");
    expect(await screen.findByPlaceholderText("Filter by workflow run id")).toBeInTheDocument();
  });

  it("lets a viewer reach the listings board", async () => {
    authState.role = "viewer";
    renderAt("/listings");
    expect(await screen.findByText("No listings found.")).toBeInTheDocument();
  });
});
