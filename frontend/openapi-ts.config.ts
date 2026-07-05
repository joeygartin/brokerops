import { defineConfig } from "@hey-api/openapi-ts";

// Generates the typed API client from the committed OpenAPI spec (which is
// itself exported from the backend's Pydantic models — see ADR-0018). The
// output in src/client/ is committed and CI-diffed: regenerate with
// `npm run generate` after any backend contract change (`make generate` at the
// repo root re-exports the spec first).
export default defineConfig({
  input: "openapi.json",
  output: "src/client",
  plugins: [
    // Bundled fetch client; runtime config wires the auth-aware fetch layer
    // (bearer + 401 refresh/replay) without touching generated files.
    { name: "@hey-api/client-fetch", runtimeConfigPath: "./src/heyApiConfig" },
    // Runtime enum objects (not just unions) so UI option lists can enumerate
    // backend enums (e.g. DocumentKind) instead of hand-copying values.
    { name: "@hey-api/typescript", enums: "javascript" },
    "@hey-api/sdk",
  ],
});
