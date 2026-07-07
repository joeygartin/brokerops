import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { TransactionDetail } from "./client";
import type { Role } from "./roles";
import { renderRouted } from "./test/renderRouted";

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

import TransactionsBoard from "./TransactionsBoard";

const DETAIL: TransactionDetail = {
  transaction: {
    id: "TXN-1001",
    listing_key: "RM1004",
    stage: "contingencies",
    parties: [{ role: "buyer", name: "Jordan Pike", contact_id: "101" }],
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
      classification: "overdue",
      days_until_due: -2,
      expected_document: "inspection_report",
      document_satisfied: false,
    },
    {
      id: "MS-1001-CLO",
      transaction_id: "TXN-1001",
      type: "closing",
      title: "Closing",
      due_date: "2026-07-29",
      status: "pending",
      owner: "Escrow",
      escalation_level: 0,
      blocked_reason: null,
      classification: "on_track",
      days_until_due: 25,
      expected_document: null,
      document_satisfied: null,
    },
  ],
  documents: [
    {
      id: "DOC-abc",
      transaction_id: "TXN-1001",
      milestone_id: null,
      kind: "purchase_agreement",
      title: "Purchase agreement.pdf",
      file: {
        file_id: "drive-0002",
        name: "Purchase agreement.pdf",
        mime_type: "application/pdf",
        size_bytes: 54,
        web_url: "http://localhost:8004/view/drive-0002",
      },
      uploaded_by: "op@example.com",
      created_at: "2026-07-04T00:00:00Z",
    },
  ],
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  roleState.role = "operator";
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation((request: Request) => {
    if (request.url.includes("/files?")) return Promise.resolve(json([]));
    return Promise.resolve(json([DETAIL]));
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TransactionsBoard documents tie-in", () => {
  it("flags a milestone whose expected document is missing", async () => {
    renderRouted(<TransactionsBoard />);
    expect(await screen.findByText("📄 needs inspection report")).toBeInTheDocument();
    // a milestone with no expectation gets no badge
    expect(screen.queryByText(/needs closing/)).not.toBeInTheDocument();
  });

  it("shows the satisfied state once the document is attached", async () => {
    const satisfied: TransactionDetail = {
      ...DETAIL,
      milestones: [
        { ...DETAIL.milestones[0], document_satisfied: true },
        DETAIL.milestones[1],
      ],
    };
    apiFetchMock.mockImplementation((request: Request) => {
      if (request.url.includes("/files?")) return Promise.resolve(json([]));
      return Promise.resolve(json([satisfied]));
    });
    renderRouted(<TransactionsBoard />);
    expect(await screen.findByText("📄 doc attached")).toBeInTheDocument();
  });

  it("switches the card to the Documents tab", async () => {
    renderRouted(<TransactionsBoard />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Documents (1)" }));
    expect(screen.getByText("Purchase agreement.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toBeInTheDocument();
    // back to the timeline
    await user.click(screen.getByRole("button", { name: "Timeline" }));
    expect(screen.getByText("Home inspection")).toBeInTheDocument();
  });
});
