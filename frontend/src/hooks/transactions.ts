import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { unwrap } from "../api";
import { API_BASE } from "../auth";
import {
  attachDocumentTransactionsTransactionIdDocumentsPost,
  getTransactionTransactionsTransactionIdGet,
  listFilesFilesGet,
  listTransactionsTransactionsGet,
  seedDemoDataDemoSeedPost,
  type AttachDocument,
} from "../client";
import { queryKeys } from "./keys";

// Transactions server state (BOP-024): the board, the demo-seed and milestone-
// cron actions, and the per-transaction document folder + attach mutation. All
// mutations invalidate the transactions query so the board reflects them; the
// cron also touches approvals (it may raise escalations).

export function useTransactions() {
  return useQuery({
    queryKey: queryKeys.transactions,
    queryFn: async () => unwrap(await listTransactionsTransactionsGet()),
    staleTime: 10_000,
  });
}

// A single transaction by id, for the deep-linkable detail route (BOP-025). Uses
// the keyed GET /transactions/{id} rather than a find() over the board query,
// which returns only active transactions — so a deep link resolves a transaction
// in any stage; an unknown id is a 404 (ApiError) the view renders as a clean
// state.
export function useTransaction(transactionId: string) {
  return useQuery({
    queryKey: queryKeys.transaction(transactionId),
    queryFn: async () =>
      unwrap(
        await getTransactionTransactionsTransactionIdGet({
          path: { transaction_id: transactionId },
        }),
      ),
    staleTime: 10_000,
  });
}

export function useSeedDemo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await seedDemoDataDemoSeedPost()),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.transactions }),
  });
}

// The milestone cron trigger is deliberately a plain fetch, not the generated
// client: /internal/cron is gated by X-Cron-Key (not the bearer), and a 401 from
// it must not trip apiFetch's refresh/sign-out path — it isn't a session problem.
// Its response is an open dict on the wire, so the summary type stays local.
export type CronSummary = {
  checked: number;
  skipped_pending_escalation: number;
  results: { transaction_id: string; status: string; outcome: string | null }[];
};

export function useRunMilestoneCron() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<CronSummary> => {
      const response = await fetch(`${API_BASE}/internal/cron/milestones`, { method: "POST" });
      if (!response.ok) throw new Error(`api returned ${response.status}`);
      return (await response.json()) as CronSummary;
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.transactions });
      client.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}

export function useFolderFiles(folder: string, options: { enabled: boolean }) {
  return useQuery({
    queryKey: queryKeys.folderFiles(folder),
    queryFn: async () => unwrap(await listFilesFilesGet({ query: { folder } })),
    enabled: options.enabled,
    staleTime: 30_000,
  });
}

export function useAttachDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { transactionId: string; body: AttachDocument }) =>
      unwrap(
        await attachDocumentTransactionsTransactionIdDocumentsPost({
          path: { transaction_id: input.transactionId },
          body: input.body,
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.transactions }),
  });
}
