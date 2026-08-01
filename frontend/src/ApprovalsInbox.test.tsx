import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { ApprovalRequest } from "./client";
import type { Role } from "./roles";
import { renderRouted } from "./test/renderRouted";

// Control the operator's role per-test and stub the data fetch. Both modules
// are hoisted mocks so ApprovalsInbox picks them up at import time. The
// generated client routes every call through apiFetch (its fetch layer), so
// stubbing ./auth intercepts the SDK's Requests too.
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

import { clearAllDrafts } from "./approvalDrafts";
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

const OUTBOUND_MESSAGE_APPROVAL: ApprovalRequest = {
  id: "ap-2",
  workflow: "vapi_followup",
  graph_thread_id: "thread-abcdef02",
  kind: "approve_outbound_message",
  payload: {
    kind: "approve_outbound_message",
    message_id: "msg-1",
    channel: "email",
    recipient: "jordan@example.test",
    subject: "Following up on your tour of RM1001",
    body: "Hi Jordan,\n\nThank you for touring RM1001.",
    template_ref: "showing_followup:v1",
    listing_key: "RM1001",
  },
  status: "pending",
  decided_by: null,
  created_at: "2026-07-04T00:00:00Z",
  decided_at: null,
};

function mockInbox(approvals: ApprovalRequest[]) {
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation((request: Request) => {
    if (request.method === "POST") {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            approval: approvals[0],
            workflow: { status: "completed", output: { outcome: "followup_sent" } },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    return Promise.resolve(
      new Response(JSON.stringify(approvals), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
}

beforeEach(() => {
  roleState.role = "admin";
  clearAllDrafts(); // module-scoped draft edits must not leak across tests
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

describe("Outbound-message card (approve_outbound_message)", () => {
  it("shows recipient, channel, and an editable draft body", async () => {
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    renderRouted(<ApprovalsInbox />);

    expect(
      await screen.findByText("Approve outbound email — jordan@example.test"),
    ).toBeInTheDocument();
    expect(screen.getByText("jordan@example.test")).toBeInTheDocument();
    const body = screen.getByLabelText("Draft body") as HTMLTextAreaElement;
    expect(body.value).toContain("Thank you for touring RM1001");
    expect(body.readOnly).toBe(false);
  });

  it("sends the edited body as edited_payload on approve", async () => {
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.change(body, { target: { value: "Edited before send." } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(async () => {
      const post = apiFetchMock.mock.calls
        .map(([request]) => request as Request)
        .find((request) => request.method === "POST");
      expect(post).toBeTruthy();
      expect(post?.url).toContain("/approvals/ap-2/decide");
      // Clone before reading: waitFor may retry and a Request body is single-use.
      expect(await post?.clone().json()).toEqual({
        decision: "approved",
        edited_payload: { body: "Edited before send." },
      });
    });
  });

  it("omits edited_payload when the body is untouched or on reject", async () => {
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.change(body, { target: { value: "Would-be edit, then rejected." } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(async () => {
      const post = apiFetchMock.mock.calls
        .map(([request]) => request as Request)
        .find((request) => request.method === "POST");
      expect(post).toBeTruthy();
      expect(await post?.clone().json()).toEqual({ decision: "rejected" });
    });
  });

  it("blocks approve while the draft body is emptied, with a hint", async () => {
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    const approve = screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(false);

    fireEvent.change(body, { target: { value: "   " } });
    expect(approve.disabled).toBe(true);
    expect(screen.getByText(/draft body is empty/)).toBeInTheDocument();
    // Reject stays available — that's the right way to discard a draft.
    expect((screen.getByRole("button", { name: "Reject" }) as HTMLButtonElement).disabled).toBe(
      false,
    );

    fireEvent.change(body, { target: { value: "Restored text." } });
    expect(approve.disabled).toBe(false);
    expect(screen.queryByText(/draft body is empty/)).not.toBeInTheDocument();
  });

  it("renders the draft read-only for a non-admin", async () => {
    roleState.role = "operator";
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    renderRouted(<ApprovalsInbox />);

    const body = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    expect(body.readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

const HOT_LEAD_APPROVAL: ApprovalRequest = {
  id: "ap-3",
  workflow: "vapi_followup",
  graph_thread_id: "thread-abcdef03",
  kind: "notify_agent",
  payload: {
    kind: "notify_agent",
    listing_key: "MLS-300",
    reason: "Ready to make an offer",
    summary: "Caller wants to move fast.",
    contact_id: "c-9",
    call_id: "call-9",
  },
  status: "pending",
  decided_by: null,
  created_at: "2026-07-02T00:00:00Z",
  decided_at: null,
};

const APPROVED_HISTORY: ApprovalRequest = {
  id: "ap-4",
  workflow: "listing_to_contract",
  graph_thread_id: "thread-abcdef04",
  kind: "approve_marketing",
  payload: { kind: "approve_marketing", listing_key: "MLS-400" },
  status: "approved",
  decided_by: "admin@example.test",
  created_at: "2026-07-01T00:00:00Z",
  decided_at: "2026-07-05T12:00:00Z",
};

const REJECTED_HISTORY: ApprovalRequest = {
  ...APPROVED_HISTORY,
  id: "ap-5",
  kind: "notify_agent",
  payload: { kind: "notify_agent", listing_key: "MLS-500" },
  status: "rejected",
  decided_by: "admin@example.test",
  decided_at: "2026-07-06T12:00:00Z",
};

// A route-aware mock: the pending list on a bare GET, per-status lists when the
// decided-history queries send ?status=, and a decision on POST.
function mockRoutes(opts: {
  pending: ApprovalRequest[];
  approved?: ApprovalRequest[];
  rejected?: ApprovalRequest[];
  postStatus?: number;
}) {
  apiFetchMock.mockReset();
  // Stateful, like the real backend: a successful decide drops the approval from
  // the pending list its next GET returns, so optimistic removal doesn't bounce
  // back on refetch. A failed decide (>=400) leaves it pending.
  const decided = new Set<string>();
  apiFetchMock.mockImplementation((request: Request) => {
    const json = (data: unknown, status = 200) =>
      Promise.resolve(
        new Response(JSON.stringify(data), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (request.method === "POST") {
      const status = opts.postStatus ?? 200;
      const match = request.url.match(/\/approvals\/([^/]+)\/decide/);
      if (match && status < 400) decided.add(match[1]);
      return json({ approval: opts.pending[0], workflow: { status: "completed", output: {} } }, status);
    }
    const status = new URL(request.url).searchParams.get("status");
    if (status === "approved") return json(opts.approved ?? []);
    if (status === "rejected") return json(opts.rejected ?? []);
    return json(opts.pending.filter((a) => !decided.has(a.id)));
  });
}

// A viewer-redacted gate: the backend caller-role egress filter (BOP-040) nulls
// the draft payload for a viewer, who may see a gate EXISTS (kind/status) but not
// its restricted content. The generated ApprovalRequest.payload is `... | null`.
const REDACTED_OUTBOUND_APPROVAL: ApprovalRequest = {
  id: "ap-r1",
  workflow: "vapi_followup",
  graph_thread_id: "thread-redacted1",
  kind: "approve_outbound_message",
  payload: null,
  status: "pending",
  decided_by: null,
  created_at: "2026-07-04T00:00:00Z",
  decided_at: null,
};

describe("Viewer-redacted approval payload (BOP-040)", () => {
  it("renders a 'content restricted to operators' state, not a crash or empty card", async () => {
    roleState.role = "viewer";
    mockInbox([REDACTED_OUTBOUND_APPROVAL]);
    renderRouted(<ApprovalsInbox />);

    // The restricted panel replaces the draft preview/form…
    expect(await screen.findByText("Content restricted to operators")).toBeInTheDocument();
    // …so no draft body/recipient leaks into the DOM, and the outbound form is absent.
    expect(screen.queryByLabelText("Draft body")).not.toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    // The heading degrades to the kind label (its detail lives in the redacted payload).
    expect(screen.getByText("Outbound message")).toBeInTheDocument();
    // A viewer sees no decision controls — the gate awaits an admin.
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText("Awaiting an admin decision.")).toBeInTheDocument();
  });
});

describe("Triage filters (BOP-028)", () => {
  it("filters the pending list by kind and shows per-kind count badges", async () => {
    mockRoutes({ pending: [MARKETING_APPROVAL, HOT_LEAD_APPROVAL, OUTBOUND_MESSAGE_APPROVAL] });
    renderRouted(<ApprovalsInbox />);

    // All three render first; the count badges reflect the unfiltered set.
    await screen.findByText(/Approve marketing/);
    const counts = screen.getByLabelText("Pending counts by kind");
    expect(counts).toHaveTextContent("Marketing 1");
    expect(counts).toHaveTextContent("Hot lead 1");
    expect(counts).toHaveTextContent("Outbound message 1");

    fireEvent.change(screen.getByRole("combobox", { name: /Kind/ }), {
      target: { value: "notify_agent" },
    });

    await waitFor(() => {
      expect(screen.getByText(/Hot lead — notify listing agent/)).toBeInTheDocument();
      expect(screen.queryByText(/Approve marketing/)).not.toBeInTheDocument();
    });
  });
});

describe("Keyboard triage flow (BOP-028)", () => {
  it("moves focus with j/k and marks the focused card", async () => {
    // Oldest-first: marketing (07-01) then hot lead (07-02).
    mockRoutes({ pending: [MARKETING_APPROVAL, HOT_LEAD_APPROVAL] });
    renderRouted(<ApprovalsInbox />);
    await screen.findByText(/Approve marketing/);

    const list = screen.getByRole("list");
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveAttribute("aria-current", "true");

    fireEvent.keyDown(list, { key: "j" });
    expect(screen.getAllByRole("listitem")[1]).toHaveAttribute("aria-current", "true");

    fireEvent.keyDown(list, { key: "k" });
    expect(screen.getAllByRole("listitem")[0]).toHaveAttribute("aria-current", "true");
  });

  it("approves the focused card on 'a' after a confirm", async () => {
    mockRoutes({ pending: [MARKETING_APPROVAL, HOT_LEAD_APPROVAL] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRouted(<ApprovalsInbox />);
    await screen.findByText(/Approve marketing/);

    fireEvent.keyDown(screen.getByRole("list"), { key: "a" });

    await waitFor(() => {
      const post = apiFetchMock.mock.calls
        .map(([request]) => request as Request)
        .find((request) => request.method === "POST");
      expect(post?.url).toContain("/approvals/ap-1/decide");
    });
    expect(confirm).toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("does not decide when the confirm is dismissed", async () => {
    mockRoutes({ pending: [MARKETING_APPROVAL] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderRouted(<ApprovalsInbox />);
    await screen.findByText(/Approve marketing/);

    fireEvent.keyDown(screen.getByRole("list"), { key: "r" });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      apiFetchMock.mock.calls.map(([r]) => r as Request).some((r) => r.method === "POST"),
    ).toBe(false);
    confirm.mockRestore();
  });

  it("ignores a/j shortcuts while typing in the draft body", async () => {
    mockRoutes({ pending: [OUTBOUND_MESSAGE_APPROVAL] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.keyDown(body, { key: "a" });

    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});

describe("Keyboard flow edge cases (BOP-028 review-gate r1)", () => {
  it("rejects a blank-draft outbound card via 'r' even though approve is blocked", async () => {
    mockRoutes({ pending: [OUTBOUND_MESSAGE_APPROVAL] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.change(body, { target: { value: "   " } }); // blank → Approve disabled

    fireEvent.keyDown(screen.getByRole("list"), { key: "r" });

    await waitFor(() => {
      const post = apiFetchMock.mock.calls
        .map(([r]) => r as Request)
        .find((r) => r.method === "POST");
      expect(post?.url).toContain("/approvals/ap-2/decide");
    });
    const post = apiFetchMock.mock.calls
      .map(([r]) => r as Request)
      .find((r) => r.method === "POST");
    expect(await post?.clone().json()).toEqual({ decision: "rejected" });
    confirm.mockRestore();
  });

  it("carries focus to the next card after a keyboard decision, so triage continues", async () => {
    // Oldest-first: marketing (ap-1, 07-01) then hot lead (ap-3, 07-02).
    mockRoutes({ pending: [MARKETING_APPROVAL, HOT_LEAD_APPROVAL] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRouted(<ApprovalsInbox />);
    await screen.findByText(/Approve marketing/);

    // Approve the focused (first) card from the keyboard; it is optimistically
    // removed and focus must land on the surviving card — not fall off the DOM.
    fireEvent.keyDown(screen.getByRole("list"), { key: "a" });

    await waitFor(() => {
      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(1);
      expect(items[0]).toHaveFocus();
    });
    // The next decision reaches the list without tabbing back in.
    fireEvent.keyDown(screen.getByRole("list"), { key: "a" });
    await waitFor(() =>
      expect(screen.getByText(/No pending approvals/)).toBeInTheDocument(),
    );
    confirm.mockRestore();
  });

  it("does not fire triage shortcuts when a card button holds focus", async () => {
    mockRoutes({ pending: [MARKETING_APPROVAL] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRouted(<ApprovalsInbox />);

    const approve = await screen.findByRole("button", { name: "Approve" });
    // The keydown bubbles to the list handler, but focus is on an interactive
    // descendant, so the shortcut must be suppressed (native button keeps the key).
    fireEvent.keyDown(approve, { key: "a" });

    expect(confirm).not.toHaveBeenCalled();
    expect(
      apiFetchMock.mock.calls.map(([r]) => r as Request).some((r) => r.method === "POST"),
    ).toBe(false);
    confirm.mockRestore();
  });
});

describe("Draft persistence across unmount (BOP-028 review-gate r2)", () => {
  it("keeps the edited draft when a failed decide rolls the card back", async () => {
    mockRoutes({ pending: [OUTBOUND_MESSAGE_APPROVAL], postStatus: 500 });
    renderRouted(<ApprovalsInbox />);

    const body = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "Carefully edited copy." } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    // The decide fails → optimistic removal rolls back → the card returns still
    // holding the operator's edit (not the original server body).
    await screen.findByText(/Failed to decide/);
    const restored = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    expect(restored.value).toBe("Carefully edited copy.");
  });

  it("keeps the edited draft across a filter change that hides then reshows it", async () => {
    mockRoutes({ pending: [OUTBOUND_MESSAGE_APPROVAL, MARKETING_APPROVAL] });
    renderRouted(<ApprovalsInbox />);

    const body = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "Draft in progress." } });

    // Filter to marketing only — the outbound card unmounts.
    fireEvent.change(screen.getByRole("combobox", { name: /Kind/ }), {
      target: { value: "approve_marketing" },
    });
    await waitFor(() =>
      expect(screen.queryByLabelText("Draft body")).not.toBeInTheDocument(),
    );

    // Back to all kinds — the outbound card returns with the edit intact.
    fireEvent.change(screen.getByRole("combobox", { name: /Kind/ }), {
      target: { value: "all" },
    });
    const restored = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    expect(restored.value).toBe("Draft in progress.");
  });

  it("still blocks a hard unload after a dirty draft is filtered out of view", async () => {
    mockRoutes({ pending: [OUTBOUND_MESSAGE_APPROVAL, MARKETING_APPROVAL] });
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.change(body, { target: { value: "Draft still in progress." } });

    // Hide the dirty card behind a filter — its own card-scoped effect (if any)
    // would be torn down, but the module-level guard reads draftStore directly.
    fireEvent.change(screen.getByRole("combobox", { name: /Kind/ }), {
      target: { value: "approve_marketing" },
    });
    await waitFor(() =>
      expect(screen.queryByLabelText("Draft body")).not.toBeInTheDocument(),
    );

    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
  });

  it("does not block a hard unload when nothing is dirty", () => {
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
  });
});

describe("Editable draft (react-hook-form, BOP-028)", () => {
  it("surfaces an unsaved-edits indicator once the body is dirty", async () => {
    mockRoutes({ pending: [OUTBOUND_MESSAGE_APPROVAL] });
    renderRouted(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    expect(screen.queryByText(/Unsaved edits/)).not.toBeInTheDocument();

    fireEvent.change(body, { target: { value: "A meaningfully edited draft." } });
    expect(await screen.findByText(/Unsaved edits/)).toBeInTheDocument();
  });
});

describe("Decided-history view (BOP-028)", () => {
  it("switches to the decided tab and lists approved/rejected with who and status", async () => {
    mockRoutes({
      pending: [MARKETING_APPROVAL],
      approved: [APPROVED_HISTORY],
      rejected: [REJECTED_HISTORY],
    });
    renderRouted(<ApprovalsInbox />);
    await screen.findByText(/Approve marketing/);

    fireEvent.click(screen.getByRole("button", { name: /Decided/ }));

    expect(await screen.findByText(/MLS-400/)).toBeInTheDocument();
    expect(screen.getByText(/MLS-500/)).toBeInTheDocument();
    expect(screen.getAllByText(/admin@example.test/).length).toBeGreaterThan(0);
    expect(screen.getByText("approved")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();
  });
});

describe("ApprovalsInbox role gating", () => {
  it("shows Approve/Reject to an admin", async () => {
    roleState.role = "admin";
    renderRouted(<ApprovalsInbox />);

    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.queryByText(/Awaiting an admin decision/)).not.toBeInTheDocument();
  });

  it("hides the decision controls from a non-admin operator", async () => {
    roleState.role = "operator";
    renderRouted(<ApprovalsInbox />);

    // The card renders (subject present) but the controls are replaced.
    await waitFor(() => expect(screen.getByText(/Approve marketing/)).toBeInTheDocument());
    expect(screen.getByText(/Awaiting an admin decision/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});
