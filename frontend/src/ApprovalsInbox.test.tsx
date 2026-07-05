import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
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
    render(<ApprovalsInbox />);

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
    render(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.change(body, { target: { value: "Edited before send." } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeTruthy();
      expect(String(post?.[0])).toContain("/approvals/ap-2/decide");
      expect(JSON.parse(String(post?.[1]?.body))).toEqual({
        decision: "approved",
        edited_payload: { body: "Edited before send." },
      });
    });
  });

  it("omits edited_payload when the body is untouched or on reject", async () => {
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    render(<ApprovalsInbox />);

    const body = await screen.findByLabelText("Draft body");
    fireEvent.change(body, { target: { value: "Would-be edit, then rejected." } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(([, init]) => init?.method === "POST");
      expect(post).toBeTruthy();
      expect(JSON.parse(String(post?.[1]?.body))).toEqual({ decision: "rejected" });
    });
  });

  it("blocks approve while the draft body is emptied, with a hint", async () => {
    mockInbox([OUTBOUND_MESSAGE_APPROVAL]);
    render(<ApprovalsInbox />);

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
    render(<ApprovalsInbox />);

    const body = (await screen.findByLabelText("Draft body")) as HTMLTextAreaElement;
    expect(body.readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
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
