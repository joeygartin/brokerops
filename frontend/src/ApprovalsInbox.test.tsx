import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { ApprovalRequest, Role } from "./types";

// Control the operator's role per-test and stub the data fetch. Both modules
// are hoisted mocks so ApprovalsInbox picks them up at import time.
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
vi.mock("./auth", () => ({ apiFetch: apiFetchMock }));

import ApprovalsInbox from "./ApprovalsInbox";

const MARKETING_APPROVAL: ApprovalRequest = {
  id: "ap-1",
  workflow: "listing_to_contract",
  graph_thread_id: "thread-abcdef01",
  kind: "approve_marketing",
  payload: {
    kind: "approve_marketing",
    listing_key: "MLS-100",
    draft: { listing_key: "MLS-100", headline: "Sunny bungalow", body: "Great home", channels: ["email"] },
  },
  status: "pending",
  decided_by: null,
  created_at: "2026-07-01T00:00:00Z",
  decided_at: null,
};

beforeEach(() => {
  roleState.role = "admin";
  apiFetchMock.mockReset();
  apiFetchMock.mockResolvedValue(
    new Response(JSON.stringify([MARKETING_APPROVAL]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ApprovalsInbox role gating", () => {
  it("shows Approve/Reject to an admin", async () => {
    roleState.role = "admin";
    render(<ApprovalsInbox />);

    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.queryByText(/Awaiting an admin decision/)).not.toBeInTheDocument();
  });

  it("hides the decision controls from a non-admin operator", async () => {
    roleState.role = "operator";
    render(<ApprovalsInbox />);

    // The card renders (subject present) but the controls are replaced.
    await waitFor(() => expect(screen.getByText(/Approve marketing/)).toBeInTheDocument());
    expect(screen.getByText(/Awaiting an admin decision/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});
