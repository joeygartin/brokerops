# Adding a selector

A **selector** is a closed-enum env var that names which backend a deploy runs —
`ORCHESTRATOR`, `EXTRACTION_BACKEND`, `EMAIL_PROVIDER`, `SMS_PROVIDER`,
`DRAFTING_BACKEND` (ADR-0014/0015) — plus the companion config each selected
backend reads. Every one is **fail-loud**: a named-but-misconfigured backend
refuses to start rather than silently downgrading to the stub. That guarantee only
holds if the value actually reaches the container process, so a selector must be
wired on **every deploy surface**.

## The five-part edit

Adding a selector (or a new companion var a selected backend fails loud without) is
one change in five places:

1. **Model** — the closed enum + `build_*` wiring in `api/src/brokerops_api/deps.py`
   (or `main.py`). Read it explicitly; never infer the backend from key presence.
2. **Selector list** — add the var to `SELECTOR_VARS` in
   `api/tests/selector_contract.py` (the single source of truth both parity tests
   share — a repo-unique-named sibling module, the workspace's collision-free pattern
   for a shared test helper; see that file's header for why not `conftest.py` or a
   package import).
3. **`.env.example`** — document the var with a fake value and its default.
4. **compose** — pass it into the api service in `docker-compose.yml` as
   `VAR: ${VAR:-default}` (compose forwards neither the host env nor `.env`
   automatically).
5. **terraform** — add an `env` block to the api container in
   `infra/modules/brokerops/services.tf`, sourced from a Terraform variable (plain
   config, threaded through `infra/variables.tf` → `infra/main.tf` →
   `infra/modules/brokerops/variables.tf`) or a Secret Manager version (secrets —
   add the secret to `client_secrets` in `secrets.tf`). Gate real backends behind
   an empty-safe condition so an unconfigured deploy is unaffected.

## What enforces it

Steps 4 and 5 are enforced automatically — `SELECTOR_VARS` drives two parity tests:

- `api/tests/test_compose_selector_parity.py` — fails if the var isn't passed into
  the compose api service.
- `api/tests/test_terraform_selector_parity.py` — fails if there is no `env` block
  naming the var in `services.tf`.

So adding the var to `SELECTOR_VARS` before wiring the surfaces turns both halves
into a red test with a message naming the missing var. Steps 1–3 are not
mechanically enforced; the parity tests exist because compose/terraform are the two
surfaces where a selector has historically shipped half-wired (BOP-016 caught
`EMAIL_PROVIDER`/`SES_*`/`SMS_*` documented and running locally but missing from
`services.tf`).
