export type ListingMedia = {
  media_key: string;
  listing_key: string;
  url: string;
  order: number;
  description: string;
};

export type Listing = {
  mls_id: string;
  status: "active" | "pending" | "closed";
  address: string;
  city: string;
  state: string;
  postal_code: string;
  list_price: number | null;
  bedrooms: number | null;
  bathrooms: number | null;
  living_area_sqft: number | null;
  year_built: number | null;
  agent_id: string;
  agent_name: string;
  remarks: string;
  modified_at: string;
  media: ListingMedia[];
};

export type MarketingDraft = {
  listing_key: string;
  headline: string;
  body: string;
  channels: string[];
};

export type EscalationMilestone = {
  id: string;
  title: string;
  due_date: string;
  days_overdue: number;
  escalation_level: number;
  note: string;
};

export type ApprovalPayload = {
  kind: string;
  listing_key?: string;
  draft?: MarketingDraft;
  transaction_id?: string;
  milestones?: EscalationMilestone[];
  contact_id?: string;
  call_id?: string;
  reason?: string;
  summary?: string;
  // approve_outbound_message (BOP-019): a drafted email awaiting the human
  // decision — the body is editable in the card and the edited text is what sends.
  message_id?: string;
  channel?: string;
  recipient?: string;
  subject?: string;
  body?: string;
  template_ref?: string;
};

export type ApprovalRequest = {
  id: string;
  workflow: string;
  graph_thread_id: string;
  kind: string;
  payload: ApprovalPayload;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  created_at: string;
  decided_at: string | null;
};

export type WorkflowRunResult = {
  thread_id: string;
  status: string;
  approval: ApprovalRequest | null;
  output: Record<string, unknown> | null;
};

export type TransactionParty = { role: string; name: string; contact_id: string | null };

// One line in the action audit-ledger: an external write the system performed
// across the MCP boundary. Mirrors core's MutationRecord.
export type MutationRecord = {
  id: string;
  workflow_run_id: string;
  workflow: string;
  tool: string;
  integration: string;
  args: Record<string, unknown>;
  approval_id: string | null;
  actor: string | null;
  outcome: "success" | "failure";
  external_id: string | null;
  error: string | null;
  created_at: string;
};

export type Transaction = {
  id: string;
  listing_key: string;
  stage: string;
  parties: TransactionParty[];
  contract_date: string;
  close_date: string | null;
};

export type MilestoneView = {
  id: string;
  transaction_id: string;
  type: string;
  title: string;
  due_date: string;
  status: string;
  owner: string;
  escalation_level: number;
  blocked_reason: string | null;
  classification: string;
  days_until_due: number;
  // BOP-021: the document kind this milestone expects, and whether one is
  // attached. document_satisfied is null when nothing is expected.
  expected_document: string | null;
  document_satisfied: boolean | null;
};

// A storage-neutral pointer into the office file store — mirrors core's FileRef.
export type FileRef = {
  file_id: string;
  name: string;
  mime_type: string;
  size_bytes: number | null;
  web_url: string;
};

export const DOCUMENT_KINDS = [
  "purchase_agreement",
  "disclosures",
  "inspection_report",
  "appraisal_report",
  "loan_approval",
  "closing_statement",
  "other",
] as const;

// Document metadata attached to a transaction — mirrors core's Document.
// Pointers only; the bytes stay in the office file store.
export type DocumentRecord = {
  id: string;
  transaction_id: string;
  milestone_id: string | null;
  kind: string;
  title: string;
  file: FileRef;
  uploaded_by: string;
  created_at: string;
};

export type TransactionDetail = {
  transaction: Transaction;
  milestones: MilestoneView[];
  documents: DocumentRecord[];
};

// Operator authorization level (mirrors core's Role). Hierarchical:
// admin > operator > viewer. The API is the security boundary; the UI uses this
// only to hide controls a role can't use.
export type Role = "viewer" | "operator" | "admin";

const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export function roleAtLeast(role: Role | null, minimum: Role): boolean {
  return role != null && ROLE_RANK[role] >= ROLE_RANK[minimum];
}

export type Principal = {
  subject: string;
  email: string;
  name: string;
  verified: boolean;
  role: Role;
};

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
