import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import type { DeadlineRow } from "./client";
import { renderRouted } from "./test/renderRouted";

// The generated client routes every call through apiFetch, so stubbing ./auth
// intercepts the deadline-queue GET.
const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("./auth", () => ({ apiFetch: apiFetchMock, API_BASE: "http://localhost:8000" }));

import DeadlineQueue from "./DeadlineQueue";

function row(overrides: Partial<DeadlineRow>): DeadlineRow {
  return {
    transaction_id: "TXN-1001",
    milestone_id: "MS-1001-INS",
    milestone_type: "inspection",
    title: "Home inspection",
    due_date: "2026-07-08",
    classification: "overdue",
    days_until_due: -2,
    listing_key: "RM1004",
    blocked_reason: null,
    ...overrides,
  };
}

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("DeadlineQueue", () => {
  it("renders the server-sorted queue, each row linking to its transaction hub", async () => {
    const queue: DeadlineRow[] = [
      row({ milestone_id: "MS-1001-INS", transaction_id: "TXN-1001", classification: "overdue", days_until_due: -2 }),
      row({
        milestone_id: "MS-1002-INS",
        transaction_id: "TXN-1002",
        listing_key: "RM1010",
        classification: "due_soon",
        days_until_due: 2,
        title: "Home inspection",
      }),
      row({
        milestone_id: "MS-1003-FIN",
        transaction_id: "TXN-1003",
        listing_key: "RM1002",
        classification: "blocked_external",
        days_until_due: 12,
        title: "Final loan docs",
        blocked_reason: "Awaiting lender underwriting update",
      }),
    ];
    apiFetchMock.mockResolvedValue(jsonOk(queue));
    renderRouted(<DeadlineQueue />);

    // The three urgency badges are present…
    expect(await screen.findByText("OVERDUE")).toBeInTheDocument();
    expect(screen.getByText("DUE SOON")).toBeInTheDocument();
    expect(screen.getByText("BLOCKED")).toBeInTheDocument();

    // …rendered in the order the server returned (most-urgent first).
    const articles = screen.getAllByRole("article");
    expect(within(articles[0]).getByText("OVERDUE")).toBeInTheDocument();
    expect(within(articles[0]).getByText(/2 days overdue/)).toBeInTheDocument();
    expect(within(articles[1]).getByText(/Due in 2 days/)).toBeInTheDocument();
    expect(within(articles[2]).getByText(/Awaiting lender underwriting update/)).toBeInTheDocument();

    // Each row links to its transaction hub.
    const openLinks = screen.getAllByRole("link", { name: "Open ↗" });
    expect(openLinks[0]).toHaveAttribute("href", "/transactions/TXN-1001");
    expect(openLinks[2]).toHaveAttribute("href", "/transactions/TXN-1003");
  });

  it("shows a proud empty state when nothing needs attention", async () => {
    apiFetchMock.mockResolvedValue(jsonOk([]));
    renderRouted(<DeadlineQueue />);

    expect(await screen.findByText("All caught up.")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});
