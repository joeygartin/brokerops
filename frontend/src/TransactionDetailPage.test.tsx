import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Role } from "./roles";

// The transaction hub (BOP-027) composed at its real deep-link route. useAuth is
// mocked so specs drive the role; ./auth is mocked so the generated client's
// fetch layer is controllable per-URL (mirrors router.test.tsx).
const authState = vi.hoisted(() => ({ role: "viewer" as Role }));
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
vi.mock("./auth", () => ({
  apiFetch: apiFetchMock,
  API_BASE: "http://localhost:8000",
  takePostLoginRedirect: () => null,
  savePostLoginRedirect: vi.fn(),
}));

import { routeTree } from "./router";

const DETAIL = {
  transaction: {
    id: "TXN-1001",
    listing_key: "RM1001",
    stage: "contingencies",
    parties: [{ name: "Sam Rivera", role: "buyer" }],
    contract_date: "2026-06-24",
    close_date: "2026-07-29",
  },
  milestones: [
    {
      id: "MS-1001-INS",
      transaction_id: "TXN-1001",
      type: "inspection",
      title: "Home inspection",
      due_date: "2026-07-02",
      status: "pending",
      owner: "Dana Whitfield",
      escalation_level: 0,
      blocked_reason: null,
      expected_document: null,
      classification: "on_track",
      days_until_due: 3,
      document_satisfied: null,
    },
  ],
  documents: [],
};

const MESSAGE = {
  id: "MSG-1",
  channel: "email",
  recipient: "dana.whitfield@example.test",
  subject: "Inspection reminder",
  body: "…",
  template_ref: "milestone_reminder:v1",
  contact_id: "",
  listing_key: "RM1001",
  transaction_id: "TXN-1001",
  status: "sent",
  provider_message_id: "prov-1",
  created_at: "2026-07-05T00:00:00Z",
  sent_at: "2026-07-05T00:00:00Z",
};

const APPROVAL = {
  id: "APV-1",
  workflow: "transaction_coordination",
  graph_thread_id: "thread-abc123",
  kind: "approve_outbound_message",
  payload: { transaction_id: "TXN-1001" },
  status: "pending",
  decided_by: null,
  created_at: "2026-07-05T00:00:00Z",
  decided_at: null,
};

const MUTATION = {
  id: "MUT-1",
  workflow_run_id: "thread-abc123",
  workflow: "transaction_coordination",
  transaction_id: "TXN-1001",
  tool: "send_email",
  integration: "email",
  // A hostile-shaped payload: even if an arg blob carried client-facing text, the
  // hub's compact audit row must never echo it (BOP-027 review r3).
  args: { recipient: "buyer@example.test", body: "SECRET client-facing message text" },
  approval_id: "APV-1",
  actor: "op@example.com",
  outcome: "success",
  external_id: "prov-9",
  error: null,
  created_at: "2026-07-05T00:00:00Z",
};

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

// Route by pathname; the hub's slices are plain paths (?transaction_id lives in
// the query string). An unmapped key falls back to an empty collection.
function fixtureFetch(fixtures: Record<string, unknown>) {
  return (request: Request): Response => {
    const path = new URL(request.url).pathname;
    if (path in fixtures) return jsonOk(fixtures[path]);
    if (path === "/transactions/TXN-1001") return new Response(null, { status: 404 });
    return jsonOk([]);
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

const FULL = {
  "/transactions/TXN-1001": DETAIL,
  "/messages": [MESSAGE],
  "/approvals": [APPROVAL],
  "/audit": [MUTATION],
};

beforeEach(() => {
  authState.role = "viewer";
  apiFetchMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TransactionDetailPage (transaction hub)", () => {
  it("composes the milestone spine, comms, related approvals, and the audit slice", async () => {
    apiFetchMock.mockImplementation((request: Request) =>
      Promise.resolve(fixtureFetch(FULL)(request)),
    );
    renderAt("/transactions/TXN-1001");

    // Milestone timeline (the spine, from the existing card).
    expect(await screen.findByText("Home inspection")).toBeInTheDocument();
    // Comms history row: recipient + status.
    expect(await screen.findByText("dana.whitfield@example.test")).toBeInTheDocument();
    // Related approval row, prettified kind, linking to its permalink.
    expect(await screen.findByText("outbound message")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Open/ });
    expect(link.getAttribute("href")).toContain("/approvals/APV-1");
    // Audit slice row: the recorded action (integration · tool), compact.
    expect(await screen.findByText(/send_email/)).toBeInTheDocument();
    // …but NEVER the raw args payload — no client-facing message text leaks into
    // the viewer-open hub (BOP-027 review r3).
    expect(screen.queryByText(/SECRET client-facing message text/)).not.toBeInTheDocument();
    expect(screen.queryByText(/buyer@example\.test/)).not.toBeInTheDocument();
  });

  it("shows a clean empty message per section for a deal with no activity", async () => {
    apiFetchMock.mockImplementation((request: Request) =>
      Promise.resolve(fixtureFetch({ "/transactions/TXN-1001": DETAIL })(request)),
    );
    renderAt("/transactions/TXN-1001");

    expect(
      await screen.findByText("No messages sent about this transaction yet."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("No approvals raised for this transaction yet."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("No recorded actions for this transaction yet."),
    ).toBeInTheDocument();
  });

  it("renders the transaction loading state while the detail is in flight", async () => {
    apiFetchMock.mockImplementation(
      (request: Request) =>
        new Promise<Response>((resolve) => {
          const path = new URL(request.url).pathname;
          // Never resolve the transaction detail; other slices can settle empty.
          if (path !== "/transactions/TXN-1001") resolve(jsonOk([]));
        }),
    );
    renderAt("/transactions/TXN-1001");

    // RouterProvider mounts asynchronously; once it does the never-resolving
    // detail fetch keeps the page in its loading state.
    expect(await screen.findByText("Loading transaction…")).toBeInTheDocument();
  });

  it("keeps the related-approval read open to a viewer, with no in-hub decide controls", async () => {
    authState.role = "viewer";
    apiFetchMock.mockImplementation((request: Request) =>
      Promise.resolve(fixtureFetch(FULL)(request)),
    );
    renderAt("/transactions/TXN-1001");

    // The viewer reads the approval and its permalink…
    expect(await screen.findByText("outbound message")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open/ })).toBeInTheDocument();
    // …but the hub itself exposes no approve/reject affordance (those live on the
    // /approvals/:id permalink under the existing admin gate).
    expect(screen.queryByRole("button", { name: /Approve|Reject/ })).not.toBeInTheDocument();
  });
});
