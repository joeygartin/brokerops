import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApprovalRequest } from "../client";
import { queryKeys } from "./keys";

// The generated client routes every call through apiFetch, so stubbing ./auth
// intercepts the decide POST (same pattern as ApprovalsInbox.test.tsx).
const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../auth", () => ({ apiFetch: apiFetchMock, API_BASE: "http://localhost:8000" }));

import { useDecideApproval } from "./approvals";

const A: ApprovalRequest = {
  id: "A",
  workflow: "listing_to_contract",
  graph_thread_id: "thread-a",
  kind: "approve_marketing",
  payload: { kind: "approve_marketing", listing_key: "MLS-1" },
  status: "pending",
  decided_by: null,
  created_at: "2026-07-01T00:00:00Z",
  decided_at: null,
};
const B: ApprovalRequest = { ...A, id: "B", graph_thread_id: "thread-b" };

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function seededClient() {
  // No active observer for the approvals query here, so onSettled's invalidate
  // marks it stale without refetching — the cache reflects onMutate/onError alone,
  // isolating the optimistic behavior from a server round-trip.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(queryKeys.approvals, [A, B]);
  return client;
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe("useDecideApproval optimistic cache", () => {
  it("removes the decided approval from the pending cache on mutate", async () => {
    apiFetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ approval: A, workflow: { status: "completed", output: {} } }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = seededClient();
    const { result } = renderHook(() => useDecideApproval(), { wrapper: wrapper(client) });

    await result.current.mutateAsync({ approvalId: "A", body: { decision: "approved" } });

    expect(client.getQueryData<ApprovalRequest[]>(queryKeys.approvals)).toEqual([B]);
  });

  it("rolls back to the pre-decision cache when the decide fails", async () => {
    apiFetchMock.mockResolvedValue(new Response("boom", { status: 500 }));
    const client = seededClient();
    const { result } = renderHook(() => useDecideApproval(), { wrapper: wrapper(client) });

    await expect(
      result.current.mutateAsync({ approvalId: "A", body: { decision: "approved" } }),
    ).rejects.toThrow();

    // The optimistic removal is undone — both approvals are back.
    expect(client.getQueryData<ApprovalRequest[]>(queryKeys.approvals)).toEqual([A, B]);
  });
});
