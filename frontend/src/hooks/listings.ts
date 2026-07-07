import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { unwrap } from "../api";
import {
  getListingListingsListingKeyGet,
  searchListingsListingsGet,
  startListingToContractWorkflowsListingToContractStartPost,
  startOutboundCallCallsOutboundPost,
  type OutboundCallResult,
  type WorkflowRunResult,
} from "../client";
import { queryKeys } from "./keys";

// Listings server state (BOP-024). Reads via useQuery over the generated client;
// the two workflow-starting actions are mutations that land an approval in the
// inbox, so they invalidate the approvals query on success.

export function useListings() {
  return useQuery({
    queryKey: queryKeys.listings,
    queryFn: async () => unwrap(await searchListingsListingsGet()),
    // Listings change rarely relative to a session — a longer window avoids
    // refetch churn while browsing.
    staleTime: 30_000,
  });
}

// A single listing by key, for the deep-linkable detail route (BOP-025). Uses
// the keyed GET /listings/{key} — NOT a find() over the board query — so a deep
// link resolves any real listing, not just those in the default search page; an
// unknown key surfaces as a 404 (ApiError) the view renders as a clean state.
export function useListing(listingKey: string) {
  return useQuery({
    queryKey: queryKeys.listing(listingKey),
    queryFn: async () =>
      unwrap(await getListingListingsListingKeyGet({ path: { listing_key: listingKey } })),
    staleTime: 30_000,
  });
}

export function useStartListingWorkflow() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (listingKey: string): Promise<WorkflowRunResult> =>
      unwrap(
        await startListingToContractWorkflowsListingToContractStartPost({
          body: { listing_key: listingKey },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.approvals }),
  });
}

export function useStartOutboundCall() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      listingKey: string;
      contactId: string;
    }): Promise<OutboundCallResult> =>
      unwrap(
        await startOutboundCallCallsOutboundPost({
          body: { listing_key: input.listingKey, contact_id: input.contactId },
        }),
      ),
    // A hot buyer surfaces as a notify_agent approval — keep the inbox fresh.
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.approvals }),
  });
}
