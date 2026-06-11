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
  bedrooms: number;
  bathrooms: number;
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

export type ApprovalRequest = {
  id: string;
  workflow: string;
  graph_thread_id: string;
  kind: string;
  payload: { kind: string; listing_key: string; draft: MarketingDraft };
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  created_at: string;
  decided_at: string | null;
};

export type WorkflowRunResult = {
  thread_id: string;
  status: string;
  approval: ApprovalRequest | null;
};

export const API_BASE = "http://localhost:8000";
