import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { ApprovalRequest } from "./client";
import type { Role } from "./roles";

// Same mocking posture as ApprovalsInbox.test: role is controllable and the
// generated client's fetch layer is stubbed via ./auth.
const roleState = vi.hoisted(() => ({ role: "admin" as Role }));
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
vi.mock("./authContext", () => ({
  useAuth: () => ({
    email: null,
    role: roleState.role,
    signOut: () => {},
    hasRole: (min: Role) => RANK[roleState.role] >= RANK[min],
  }),
}));

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("./auth", () => ({ apiFetch: apiFetchMock, API_BASE: "http://localhost:8000" }));

import ApprovalDetail from "./ApprovalDetail";
import { clearAllDrafts } from "./approvalDrafts";

function outbound(id: string, body: string): ApprovalRequest {
  return {
    id,
    workflow: "vapi_followup",
    graph_thread_id: `thread-${id}`,
    kind: "approve_outbound_message",
    payload: {
      kind: "approve_outbound_message",
      message_id: `msg-${id}`,
      channel: "email",
      recipient: `${id}@example.test`,
      subject: `Subject ${id}`,
      body,
      listing_key: `L-${id}`,
    },
    status: "pending",
    decided_by: null,
    created_at: "2026-07-04T00:00:00Z",
    decided_at: null,
  };
}

const TWO = outbound("ap-2", "Body for TWO");
const SEVEN = outbound("ap-7", "Body for SEVEN");

beforeEach(() => {
  roleState.role = "admin";
  clearAllDrafts();
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation((request: Request) => {
    const json = (data: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(data), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (request.method === "POST") {
      const id = request.url.match(/\/approvals\/([^/]+)\/decide/)?.[1];
      const approval = id === "ap-7" ? SEVEN : TWO;
      return json({
        approval: { ...approval, status: "approved" },
        workflow: { status: "completed", output: {} },
      });
    }
    return json(request.url.endsWith("/ap-7") ? SEVEN : TWO);
  });
});

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const rootRoute = createRootRoute();
  const detail = createRoute({
    getParentRoute: () => rootRoute,
    path: "/approvals/$id",
    component: ApprovalDetail,
  });
  const list = createRoute({
    getParentRoute: () => rootRoute,
    path: "/approvals",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([detail, list]),
    history: createMemoryHistory({ initialEntries: ["/approvals/ap-2"] }),
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("ApprovalDetail permalink reuse (BOP-028 review-gate r6)", () => {
  it("shows and submits only the current approval's body when moving between permalinks", async () => {
    const router = renderDetail();

    const first = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    expect(first.value).toContain("Body for TWO");

    // Navigate straight to another cached approval permalink — the card must
    // remount and show ap-7's body, not carry ap-2's over.
    await act(async () => {
      await router.navigate({ to: "/approvals/$id", params: { id: "ap-7" } });
    });

    await waitFor(() => {
      const body = screen.getByLabelText("Draft body") as HTMLTextAreaElement;
      expect(body.value).toContain("Body for SEVEN");
      expect(body.value).not.toContain("Body for TWO");
    });

    // Approving ap-7 untouched must send no edited_payload — proving the form is
    // not holding ap-2's stale body.
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => {
      const post = apiFetchMock.mock.calls
        .map(([r]) => r as Request)
        .find((r) => r.method === "POST");
      expect(post?.url).toContain("/approvals/ap-7/decide");
    });
    const post = apiFetchMock.mock.calls
      .map(([r]) => r as Request)
      .find((r) => r.method === "POST");
    expect(await post?.clone().json()).toEqual({ decision: "approved" });
  });
});
