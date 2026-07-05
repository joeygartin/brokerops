# brokerops frontend

React 19 + Vite + TypeScript operator console.

## Commands

- `npm ci` — install
- `npm run dev` — dev server on :5173 (API expected on :8000; `VITE_API_BASE` overrides)
- `npm test` — vitest suite
- `npm run build` — typecheck + production build
- `npm run generate` — regenerate the API client from the committed `openapi.json`

## Generated API client (ADR-0018)

`src/client/` is **generated** by [`@hey-api/openapi-ts`](https://heyapi.dev) from
`openapi.json`, which is itself exported from the backend's Pydantic models — the
single source of truth for the wire contract. Do not hand-edit anything under
`src/client/`.

After any backend change that touches a route or model shape, regenerate from the
repo root:

```sh
make generate   # exports openapi.json from the API, then runs `npm run generate`
```

and commit the diff. CI's `contract-drift` job regenerates and fails on any
difference, so a contract change without regeneration cannot merge.

The client's fetch layer is `apiFetch` (`src/auth.ts`, wired in
`src/heyApiConfig.ts`): every generated call carries the bearer token and the
401 refresh-and-replay behavior. The generator version is pinned exactly in
`package.json` because the generated output is byte-compared in CI.
