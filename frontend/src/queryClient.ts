import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api";

// The single QueryClient for the app (BOP-024, ADR-0022). Server state lives
// here — caching, retries, invalidation, and the Approvals inbox's polling —
// while local UI state stays in React and the session/bearer stays in the auth
// context. No Redux/Zustand: server state is not client state.
//
// Defaults: retry off for 4xx (a rejected request won't succeed on a replay;
// only transient 5xx/network failures are worth retrying), and a modest global
// staleTime that each resource hook overrides for its own liveness needs.
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            return false;
          }
          return failureCount < 2;
        },
      },
    },
  });
}
