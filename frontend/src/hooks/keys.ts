// Central query-key registry (BOP-024). One place so a mutation can invalidate
// exactly the queries it affects without stringly-typed keys drifting apart.
export const queryKeys = {
  listings: ["listings"] as const,
  transactions: ["transactions"] as const,
  approvals: ["approvals"] as const,
  audit: (workflowRunId: string) => ["audit", workflowRunId] as const,
  folderFiles: (folder: string) => ["files", folder] as const,
};
