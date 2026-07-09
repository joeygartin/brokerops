import { describe, expect, it } from "vitest";
import type { ApprovalRequest } from "./client";
import {
  countByKind,
  distinctKinds,
  distinctWorkflows,
  filterApprovals,
  kindLabel,
  NO_FILTERS,
  sortByDecidedAt,
  sortForTriage,
} from "./approvalTriage";

// A terse builder — only the fields the triage helpers read.
function approval(over: Partial<ApprovalRequest> & Pick<ApprovalRequest, "id">): ApprovalRequest {
  return {
    workflow: "listing_to_contract",
    graph_thread_id: "thread-00000000",
    kind: "approve_marketing",
    payload: {},
    status: "pending",
    decided_by: null,
    created_at: "2026-07-01T00:00:00Z",
    decided_at: null,
    ...over,
  } as ApprovalRequest;
}

const NOW = new Date("2026-07-10T00:00:00Z");

describe("kindLabel", () => {
  it("maps known kinds and falls back to the raw wire value", () => {
    expect(kindLabel("approve_outbound_message")).toBe("Outbound message");
    expect(kindLabel("notify_agent")).toBe("Hot lead");
    expect(kindLabel("some_new_gate")).toBe("some_new_gate");
  });
});

describe("distinct + counts", () => {
  const set = [
    approval({ id: "1", kind: "approve_marketing", workflow: "listing_to_contract" }),
    approval({ id: "2", kind: "approve_marketing", workflow: "vapi_followup" }),
    approval({ id: "3", kind: "notify_agent", workflow: "vapi_followup" }),
  ];

  it("lists distinct kinds and workflows in first-seen order", () => {
    expect(distinctKinds(set)).toEqual(["approve_marketing", "notify_agent"]);
    expect(distinctWorkflows(set)).toEqual(["listing_to_contract", "vapi_followup"]);
  });

  it("counts per kind over the whole set", () => {
    expect(countByKind(set)).toEqual({ approve_marketing: 2, notify_agent: 1 });
  });
});

describe("filterApprovals", () => {
  const set = [
    approval({ id: "new", kind: "approve_marketing", created_at: "2026-07-09T00:00:00Z" }),
    approval({ id: "old", kind: "notify_agent", created_at: "2026-07-01T00:00:00Z" }),
  ];

  it("is a no-op with the default filters", () => {
    expect(filterApprovals(set, NO_FILTERS, NOW).map((a) => a.id)).toEqual(["new", "old"]);
  });

  it("filters by kind", () => {
    expect(
      filterApprovals(set, { ...NO_FILTERS, kind: "notify_agent" }, NOW).map((a) => a.id),
    ).toEqual(["old"]);
  });

  it("filters by age (keeps only items at least N days old)", () => {
    // 'new' is 1 day old, 'old' is 9 days old.
    expect(filterApprovals(set, { ...NO_FILTERS, age: "over3d" }, NOW).map((a) => a.id)).toEqual([
      "old",
    ]);
    expect(filterApprovals(set, { ...NO_FILTERS, age: "over7d" }, NOW).map((a) => a.id)).toEqual([
      "old",
    ]);
  });
});

describe("sortForTriage", () => {
  it("orders non-escalation gates oldest-first", () => {
    const set = [
      approval({ id: "mid", created_at: "2026-07-05T00:00:00Z" }),
      approval({ id: "oldest", created_at: "2026-07-01T00:00:00Z" }),
      approval({ id: "newest", created_at: "2026-07-09T00:00:00Z" }),
    ];
    expect(sortForTriage(set).map((a) => a.id)).toEqual(["oldest", "mid", "newest"]);
  });

  it("floats the most-overdue escalations above older non-escalations", () => {
    const set = [
      approval({ id: "old-marketing", created_at: "2026-07-01T00:00:00Z" }),
      approval({
        id: "urgent-escalation",
        kind: "approve_escalation",
        created_at: "2026-07-08T00:00:00Z",
        payload: { milestones: [{ days_overdue: 2 }, { days_overdue: 12 }] },
      }),
      approval({
        id: "mild-escalation",
        kind: "approve_escalation",
        created_at: "2026-07-07T00:00:00Z",
        payload: { milestones: [{ days_overdue: 3 }] },
      }),
    ];
    // Escalations first, by descending days_overdue (12 then 3), then the
    // oldest-first non-escalation.
    expect(sortForTriage(set).map((a) => a.id)).toEqual([
      "urgent-escalation",
      "mild-escalation",
      "old-marketing",
    ]);
  });

  it("breaks equal-urgency, equal-timestamp ties deterministically by id", () => {
    const created = "2026-07-04T00:00:00Z";
    const forward = [
      approval({ id: "c", created_at: created }),
      approval({ id: "a", created_at: created }),
      approval({ id: "b", created_at: created }),
    ];
    // Same order regardless of incoming fetch order — a valid total order.
    const reverse = [...forward].reverse();
    expect(sortForTriage(forward).map((a) => a.id)).toEqual(["a", "b", "c"]);
    expect(sortForTriage(reverse).map((a) => a.id)).toEqual(["a", "b", "c"]);
  });

  it("does not mutate its input", () => {
    const set = [approval({ id: "b" }), approval({ id: "a" })];
    const snapshot = set.map((a) => a.id);
    sortForTriage(set);
    expect(set.map((a) => a.id)).toEqual(snapshot);
  });
});

describe("sortByDecidedAt", () => {
  it("orders decided approvals most-recent first", () => {
    const set = [
      approval({ id: "early", status: "approved", decided_at: "2026-07-02T00:00:00Z" }),
      approval({ id: "late", status: "rejected", decided_at: "2026-07-08T00:00:00Z" }),
    ];
    expect(sortByDecidedAt(set).map((a) => a.id)).toEqual(["late", "early"]);
  });
});
