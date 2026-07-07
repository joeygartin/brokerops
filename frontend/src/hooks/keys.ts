// Central query-key registry (BOP-024). One place so a mutation can invalidate
// exactly the queries it affects without stringly-typed keys drifting apart.
export const queryKeys = {
  listings: ["listings"] as const,
  transactions: ["transactions"] as const,
  approvals: ["approvals"] as const,
  // Per-entity detail queries (BOP-025). Nested under the collection key so a
  // mutation invalidating the collection (prefix match) also refreshes an open
  // detail view — e.g. deciding an approval refetches its permalink.
  listing: (listingKey: string) => ["listings", listingKey] as const,
  transaction: (transactionId: string) => ["transactions", transactionId] as const,
  approval: (approvalId: string) => ["approvals", approvalId] as const,
  audit: (workflowRunId: string) => ["audit", workflowRunId] as const,
  folderFiles: (folder: string) => ["files", folder] as const,
};
