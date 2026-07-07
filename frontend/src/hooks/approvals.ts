import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { unwrap } from "../api";
import {
  decideApprovalApprovalsApprovalIdDecidePost,
  listApprovalsApprovalsGet,
  type DecideRequest,
  type DecisionResponse,
} from "../client";
import { queryKeys } from "./keys";

// Approvals server state (BOP-024). The inbox is the one view that wants
// live-ish updates, so it polls; a decision invalidates both the inbox and the
// transactions board (a resolved escalation/marketing draft changes the related
// transaction).

export function useApprovals() {
  return useQuery({
    queryKey: queryKeys.approvals,
    queryFn: async () => unwrap(await listApprovalsApprovalsGet()),
    // Always considered stale so a poll actually refetches.
    staleTime: 0,
    // Poll every 7s for near-live inbox updates. refetchIntervalInBackground is
    // false (the default) so the timer pauses while the tab is hidden.
    refetchInterval: 7_000,
    refetchIntervalInBackground: false,
  });
}

export function useDecideApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      approvalId: string;
      body: DecideRequest;
    }): Promise<DecisionResponse> =>
      unwrap(
        await decideApprovalApprovalsApprovalIdDecidePost({
          path: { approval_id: input.approvalId },
          body: input.body,
        }),
      ),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: queryKeys.transactions });
    },
  });
}
