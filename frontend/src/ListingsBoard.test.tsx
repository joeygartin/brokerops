import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import type { Listing } from "./client";
import type { Role } from "./roles";
import { renderWithClient } from "./test/renderWithClient";

const roleState = vi.hoisted(() => ({ role: "operator" as Role }));
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

vi.mock("./authContext", () => ({
  useAuth: () => ({
    email: null,
    role: roleState.role,
    signOut: () => {},
    hasRole: (min: Role) => RANK[roleState.role] >= RANK[min],
  }),
}));

// The generated client routes every call through apiFetch (its fetch layer),
// so stubbing ./auth intercepts the SDK's Requests too.
const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("./auth", () => ({ apiFetch: apiFetchMock, API_BASE: "http://localhost:8000" }));

import ListingsBoard from "./ListingsBoard";

const ACTIVE_LISTING: Listing = {
  mls_id: "MLS-100",
  status: "active",
  address: "1 Sunny St",
  city: "Redding",
  state: "CA",
  postal_code: "96001",
  list_price: 450000,
  bedrooms: 3,
  bathrooms: 2,
  living_area_sqft: 1800,
  year_built: 1998,
  agent_id: "a1",
  agent_name: "Pat Agent",
  remarks: "",
  modified_at: "2026-07-01T00:00:00Z",
  media: [],
};

beforeEach(() => {
  roleState.role = "operator";
  apiFetchMock.mockReset();
  apiFetchMock.mockResolvedValue(
    new Response(JSON.stringify([ACTIVE_LISTING]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ListingsBoard role gating", () => {
  it("shows the workflow controls to an operator on an active listing", async () => {
    roleState.role = "operator";
    renderWithClient(<ListingsBoard />);

    expect(
      await screen.findByRole("button", { name: "Start marketing workflow" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Feedback call" })).toBeInTheDocument();
  });

  it("hides the workflow controls from a viewer", async () => {
    roleState.role = "viewer";
    renderWithClient(<ListingsBoard />);

    // The card still renders (address present) — only the action row is gated.
    await waitFor(() => expect(screen.getByText("1 Sunny St")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Start marketing workflow" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Feedback call" })).not.toBeInTheDocument();
  });
});
