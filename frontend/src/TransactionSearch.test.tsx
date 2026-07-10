import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { TransactionSearchRow } from "./client";
import { renderRouted } from "./test/renderRouted";

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("./auth", () => ({ apiFetch: apiFetchMock, API_BASE: "http://localhost:8000" }));

import TransactionSearch from "./TransactionSearch";

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const RESULT: TransactionSearchRow = {
  property_address: "951 Fox Hollow Way, Rivermouth, CA 95890",
  transaction: {
    tenant_id: "",
    id: "TXN-1002",
    listing_key: "RM1010",
    stage: "under_contract",
    parties: [{ role: "buyer", name: "Casey Romero", contact_id: "102", email: "" }],
    contract_date: "2026-07-01",
    close_date: "2026-08-01",
  },
};

async function submitSearch(term: string) {
  // renderRouted mounts the router asynchronously — wait for the input to exist.
  const input = await screen.findByLabelText("Search transactions");
  fireEvent.change(input, { target: { value: term } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("TransactionSearch", () => {
  it("prompts before a query and does not fetch", async () => {
    renderRouted(<TransactionSearch />);

    expect(await screen.findByText("Search your active transactions.")).toBeInTheDocument();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("renders matching transactions with address and a hub link on submit", async () => {
    apiFetchMock.mockResolvedValue(jsonOk([RESULT]));
    renderRouted(<TransactionSearch />);

    await submitSearch("fox hollow");

    expect(await screen.findByText("TXN-1002 — RM1010")).toBeInTheDocument();
    expect(screen.getByText("951 Fox Hollow Way, Rivermouth, CA 95890")).toBeInTheDocument();
    expect(screen.getByText(/Casey Romero \(buyer\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open ↗" })).toHaveAttribute(
      "href",
      "/transactions/TXN-1002",
    );
    // The query reached the search endpoint.
    const requestedUrl = (apiFetchMock.mock.calls[0][0] as Request).url;
    expect(requestedUrl).toContain("/transactions/search");
    expect(decodeURIComponent(requestedUrl)).toContain("q=fox hollow");
  });

  it("shows a no-results state for a term that matches nothing", async () => {
    apiFetchMock.mockResolvedValue(jsonOk([]));
    renderRouted(<TransactionSearch />);

    await submitSearch("nothing-here");

    expect(await screen.findByText('No transactions match "nothing-here".')).toBeInTheDocument();
  });

  it("does not fire for a blank submission", async () => {
    renderRouted(<TransactionSearch />);

    await submitSearch("   ");

    await waitFor(() =>
      expect(screen.getByText("Search your active transactions.")).toBeInTheDocument(),
    );
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
