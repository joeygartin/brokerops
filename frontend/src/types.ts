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
  list_price: number;
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
};

export type TransactionDetail = {
  transaction: Transaction;
  milestones: MilestoneView[];
};

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
