import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DocumentRecord, FileRef, MilestoneView, Role, Transaction } from "./types";

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

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("./auth", () => ({ apiFetch: apiFetchMock }));

import TransactionDocuments from "./TransactionDocuments";

const TXN: Transaction = {
  id: "TXN-1001",
  listing_key: "RM1004",
  stage: "contingencies",
  parties: [],
  contract_date: "2026-06-24",
  close_date: "2026-07-29",
};

const MILESTONE: MilestoneView = {
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
};

const FOLDER_FILES: FileRef[] = [
  {
    file_id: "drive-0002",
    name: "Purchase agreement.pdf",
    mime_type: "application/pdf",
    size_bytes: 54,
    web_url: "http://localhost:8004/view/drive-0002",
  },
  {
    file_id: "drive-0004",
    name: "Home inspection report.pdf",
    mime_type: "application/pdf",
    size_bytes: 55,
    web_url: "http://localhost:8004/view/drive-0004",
  },
];

const ATTACHED: DocumentRecord = {
  id: "DOC-abc",
  transaction_id: "TXN-1001",
  milestone_id: null,
  kind: "purchase_agreement",
  title: "Purchase agreement.pdf",
  file: FOLDER_FILES[0],
  uploaded_by: "op@example.com",
  created_at: "2026-07-04T00:00:00Z",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  roleState.role = "operator";
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation((url: string) => {
    if (url.includes("/files?")) return Promise.resolve(json(FOLDER_FILES));
    return Promise.resolve(json(ATTACHED, 201));
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TransactionDocuments", () => {
  it("lists attached documents with an open link", async () => {
    render(
      <TransactionDocuments
        transaction={TXN}
        documents={[ATTACHED]}
        milestones={[MILESTONE]}
        onChanged={() => {}}
      />,
    );
    // let the on-mount folder browse settle before asserting
    await screen.findByRole("combobox", { name: "File to attach" });
    expect(screen.getByText("Purchase agreement.pdf")).toBeInTheDocument();
    expect(screen.getByText("PURCHASE AGREEMENT")).toBeInTheDocument();
    const open = screen.getByRole("link", { name: "Open" });
    expect(open).toHaveAttribute("href", "http://localhost:8004/view/drive-0002");
    expect(open).toHaveAttribute("target", "_blank");
  });

  it("shows the attach controls to an operator and posts the attachment", async () => {
    const onChanged = vi.fn();
    render(
      <TransactionDocuments
        transaction={TXN}
        documents={[]}
        milestones={[MILESTONE]}
        onChanged={onChanged}
      />,
    );

    // folder is browsed via the transaction's listing key
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(expect.stringContaining("/files?folder=RM1004")),
    );

    const user = userEvent.setup();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "File to attach" }),
      "drive-0004",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Document kind" }),
      "inspection_report",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Attach to milestone" }),
      "MS-1001-INS",
    );
    await user.click(screen.getByRole("button", { name: "Attach" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const attachCall = apiFetchMock.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].endsWith("/transactions/TXN-1001/documents"),
    );
    expect(attachCall).toBeDefined();
    const [url, init] = attachCall as [string, RequestInit];
    expect(url).toContain("/transactions/TXN-1001/documents");
    expect(JSON.parse(String(init.body))).toEqual({
      file_id: "drive-0004",
      kind: "inspection_report",
      milestone_id: "MS-1001-INS",
    });
  });

  it("already-attached files are not offered again", async () => {
    render(
      <TransactionDocuments
        transaction={TXN}
        documents={[ATTACHED]}
        milestones={[MILESTONE]}
        onChanged={() => {}}
      />,
    );
    const select = await screen.findByRole("combobox", { name: "File to attach" });
    const names = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(names).toContain("Home inspection report.pdf");
    expect(names).not.toContain("Purchase agreement.pdf"); // already attached
  });

  it("hides the attach controls from a viewer (list stays visible)", () => {
    roleState.role = "viewer";
    render(
      <TransactionDocuments
        transaction={TXN}
        documents={[ATTACHED]}
        milestones={[MILESTONE]}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("Purchase agreement.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach" })).not.toBeInTheDocument();
    // viewers don't browse the office folder at all
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
