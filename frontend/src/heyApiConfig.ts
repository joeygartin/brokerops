import type { CreateClientConfig } from "./client/client.gen";
import { API_BASE, apiFetch } from "./auth";

// Runtime configuration for the generated API client (referenced from
// openapi-ts.config.ts via `runtimeConfigPath`, so it is applied at client
// creation — no import-order footgun). apiFetch is the fetch layer: every
// generated call gets the bearer header and the 401 refresh-and-replay
// behavior for free (ADR-0018).
export const createClientConfig: CreateClientConfig = (config) => ({
  ...config,
  baseUrl: API_BASE,
  fetch: apiFetch,
});
