import { useQuery } from "@tanstack/react-query";
import { unwrap } from "../api";
import { listMutationsAuditGet } from "../client";
import { queryKeys } from "./keys";

// Audit-trail server state (BOP-024). Keyed by the run-id filter so typing a
// filter refetches the scoped list; the view's Refresh button re-runs the same
// query via the returned refetch().
export function useMutations(workflowRunId: string) {
  const trimmed = workflowRunId.trim();
  return useQuery({
    queryKey: queryKeys.audit(trimmed),
    queryFn: async () =>
      unwrap(await listMutationsAuditGet({ query: { workflow_run_id: trimmed || undefined } })),
    staleTime: 5_000,
  });
}
