import { useQuery } from "@tanstack/react-query";
import { unwrap } from "../api";
import { listMessagesMessagesGet } from "../client";
import { queryKeys } from "./keys";

// Outbound comms server state for the transaction hub (BOP-027). The comms
// history filtered to one deal via GET /messages?transaction_id=, so the hub can
// show what has actually been sent about this transaction (channel/status/
// recipient). Read-open like the audit trail; recipient PII is redacted at the
// egress boundary by role (BOP-012), so no role handling is needed here.
export function useTransactionMessages(transactionId: string) {
  return useQuery({
    queryKey: queryKeys.transactionMessages(transactionId),
    queryFn: async () =>
      unwrap(await listMessagesMessagesGet({ query: { transaction_id: transactionId } })),
    staleTime: 10_000,
  });
}
