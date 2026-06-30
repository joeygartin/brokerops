# brokerops

[![ci](https://github.com/joeygartin/brokerops/actions/workflows/ci.yml/badge.svg)](https://github.com/joeygartin/brokerops/actions/workflows/ci.yml)

**AI-powered backoffice for real estate brokerages** — listing-to-contract
marketing, deadline-driven transaction coordination, and voice follow-up, with a
human approval gate in front of every consequential action.

<!-- TODO(joey): 60–90s screen recording of docs/DEMO.md goes here -->

Three workflows over an MCP tool boundary, a framework-free domain core, durable
human-in-the-loop (Postgres-backed — approvals survive restarts and deploys), and
per-client GCP deploys via Terraform. The orchestration layer is **dual-engine**:
the same workflows run on LangGraph or Google ADK, selected by
`ORCHESTRATOR=langgraph|adk` (default `langgraph`), and CI proves both against the
same end-to-end demo script on every push.

| Workflow | Trigger | Human gate |
|---|---|---|
| `listing_to_contract` | UI / new listing | approve the marketing draft → CRM task fan-out |
| `transaction_coordination` | Cloud Scheduler (daily) | approve overdue-milestone escalations → URGENT tasks, level ratchet |
| `vapi_followup` | end-of-call webhook | hot-lead alert when a buyer signals offer intent |

## Try it (zero credentials)

The only prerequisite is Docker (with Compose). No language toolchain, no API keys.

```bash
git clone https://github.com/joeygartin/brokerops && cd brokerops
make demo
```

`make demo` builds the stack, starts it detached, and seeds demo data. Then open
<http://localhost:5173> and follow **[docs/DEMO.md](docs/DEMO.md)** — a
scripted 5-minute path through all three workflows. The MLS (a genuine RESO Web API
OData subset), the CRM, and the voice platform are bundled stubs that speak the real
APIs' shapes; the same path is asserted in CI by `scripts/e2e_demo_check.sh`.

The durability party trick: start a workflow, `docker compose restart api` while
it waits for your approval, and approve it afterward — the workflow resumes from
its Postgres checkpoint in the new process.

## Architecture

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full picture and
**[docs/ADRs/](docs/ADRs/)** for the decisions. The short version:

- **`core/`** is plain Python + Pydantic — no LangGraph, no ADK, no FastAPI, no
  SDKs. Workflow nodes are thin; business rules (marketing drafts, milestone date
  math, transcript extraction — including a spoken price-range parser) live in core
  services and are unit-tested in isolation.
- **Every integration is three things:** an adapter implementing a core `Protocol`
  port, a recorded-shape stub, and an MCP server (`uv run mcp-server-mls-reso`,
  `…-followupboss`, `…-vapi`). Swapping the mock MLS for a live RESO feed is a
  base-URL + auth change, pinned by contract tests.
- **Every human gate is the same spine:** a workflow interrupt → an
  `ApprovalRequest` row → the Approval Inbox → one resume endpoint. That uniformity
  is what made the orchestrator swappable — and V2 swapped it (ADR-0004).
- **The dual-engine setup is CI-proven, not claimed:** every push runs mirrored
  scenario suites and a Postgres restart-survival proof for each engine
  (`orchestration/langgraph/`, `orchestration/adk/`), then the same e2e demo
  script as a `{langgraph, adk}` matrix against the full compose stack. Both
  engines must stay green for `main` to be green.

## Development

Working on the code (rather than just running it) needs [uv](https://docs.astral.sh/uv/)
and Python 3.12+; tests that exercise restart-survival also need a local Postgres
(the compose stack provides one).

```bash
uv sync --all-packages   # install workspace deps
make test                # ~280 tests (contract, workflow x2 engines, API flow, auth,
                         # audit ledger, idempotency, transaction open, tenant scoping,
                         # restart-survival)
ORCHESTRATOR=adk make demo   # the same demo on the ADK engine
make lint                # ruff + mypy strict
```

## Deploying to GCP

Each deploy is a self-contained per-client stack: two Cloud Run services (api +
frontend), a Cloud SQL Postgres instance, Secret Manager shells (keys pushed
interactively, never through the repo or tfstate), and the daily milestone Cloud
Scheduler job. Everything is Terraform; the only state lives in a GCS bucket. The
fastest first deploy is the bundled `demo` client — it runs all three integrations
as in-process stubs, so it reaches the cloud with **zero external credentials**.

### Prerequisites

- The [`gcloud` CLI](https://cloud.google.com/sdk/docs/install), authenticated:
  `gcloud auth login` (and `gcloud auth application-default login`).
- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5.
- Docker (to build and push the images), plus `make`.
- A GCP project with billing linked, and a billing-enabled account:
  ```bash
  gcloud projects create <project-id>
  gcloud billing projects link <project-id> --billing-account=<ACCOUNT_ID>
  ```

### 1. Bootstrap the project (once per GCP project)

Enables the required APIs (Cloud Run, Cloud SQL, Secret Manager, Scheduler,
Artifact Registry, IAM), creates the `brokerops` Artifact Registry repo, the
Terraform state bucket (versioned), and configures Docker auth:

```bash
make gcp-bootstrap GCP_PROJECT=<project-id> GCP_REGION=us-west1 TF_STATE_BUCKET=<bucket>
```

### 2. Describe the client (committed, no secrets)

```bash
cp infra/clients/_template.tfvars infra/clients/acme.tfvars
```

Edit `acme.tfvars`: set `client_name`, `project_id`, and the `api_image` /
`frontend_image` registry paths (just substitute your `project_id`). These files
are committed — **never put secrets here**; secrets go to Secret Manager in step 5.
Real integrations, the workflow engine, auth, and RBAC are all configured here too
(see [Configuration reference](#configuration-reference)).

### 3. Build & push images

```bash
make gcp-images CLIENT=acme
```

Builds and pushes `api:latest` and `frontend:latest` to Artifact Registry
(`linux/amd64`). The frontend is served same-origin and proxies `/api` to the api
service at runtime (ADR-0003), so one image works across clients.

### 4. Deploy

```bash
TF_STATE_BUCKET=<bucket> make deploy CLIENT=acme
```

Runs `terraform init` (pointing at the client's state prefix) then `apply` with
`infra/clients/acme.tfvars`. On success Terraform prints the Cloud Run URLs.

> **Note on `:latest` images.** Terraform pins images by the `:latest` tag, so a
> later same-tag re-push does **not** roll a new revision on its own. After
> rebuilding images for an existing client, force a new revision:
> `gcloud run deploy brokerops-acme-api --image <registry>/api:latest --region us-west1 --project <project-id>` (and likewise `…-frontend`).

### 5. Push real integration keys (only if flipping integrations live)

```bash
make secrets CLIENT=acme
```

Prompts for each key and writes it straight to Secret Manager — values never touch
the repo or tfstate. Press Enter to skip any you don't need (the stubs need none).
Then redeploy (step 4) so the new revision picks them up.

### Configuration reference

**`<client>.tfvars` (committed — non-secret config):**

| Variable | Purpose |
|---|---|
| `client_name`, `project_id`, `region` | Identity + GCP target (`region` default `us-west1`). |
| `api_image`, `frontend_image` | Artifact Registry image paths. |
| `orchestrator` | `langgraph` (default) or `adk` (ADR-0004). |
| `reso_base_url`, `fub_base_url`, `vapi_base_url` | `internal` (default) runs the bundled stub; set a real base URL to go live. |
| `vapi_assistant_id` | Vapi assistant for outbound calls (ADR-0005). |
| `enable_llm_extraction`, `llm_model` | Flip feedback extraction to Claude (ADR-0006); needs the `llm-api-key` secret. |
| `enable_auth`, `auth_methods` | Turn on operator login; `auth_methods` is `google`, `magic`, or both (ADR-0007/0008). |
| `auth_allowed_domain`, `auth_allowed_emails` | Who may sign in (shared by both methods). |
| `auth_admin_emails/domain`, `auth_viewer_emails/domain` | RBAC roles (ADR-0009). None set → every operator is admin. |
| `google_oidc_client_id` | Google OAuth **web** client id (public, not a secret). |
| `public_base_url`, `smtp_host`, `smtp_port`, `smtp_from`, `smtp_username` | Magic-link delivery; no `smtp_host` → the link is logged to the api console. |
| `enable_langsmith` | Optional LangSmith tracing. |
| `cron_schedule` | Daily milestone job cron (default `0 13 * * *`). |

**Secrets (pushed via `make secrets`, never committed):** `fub-api-key`,
`vapi-api-key`, `vapi-webhook-secret`, `reso-auth-token`, `llm-api-key`,
`smtp-password`, `langsmith-api-key`. Each is a no-op for the bundled stubs.

### Self-contained demo deploy

`infra/clients/demo.tfvars` deploys the whole stack with every integration on its
in-process stub — zero secrets, even in the cloud:

```bash
make gcp-images CLIENT=demo
TF_STATE_BUCKET=<bucket> make deploy CLIENT=demo
```

To layer operator auth onto the demo, set the `enable_auth` / `auth_methods` /
allowlist / RBAC values in `demo.tfvars` (or pass them as `-var` overrides) and add
the `smtp-password` secret for magic-link email.

## Layout

```
core/                    # framework-free domain: models, services, ports
integrations/            # mls_reso · followupboss · vapi (adapter + stub + MCP server each)
                         #   · llm_extraction (LLM adapter) · google_oidc · email_smtp (operator auth)
orchestration/           # the three workflows, twice: langgraph/ (V1) + adk/ (V2)
api/                     # FastAPI: routes, webhooks, cron, workflow engine, Alembic
frontend/                # React + Vite: Listings, Transactions, Approval Inbox
infra/                   # Terraform per-client module + bootstrap
docs/                    # ARCHITECTURE.md · DEMO.md · CLIENT_ONBOARDING.md · ADRs/
```

## Status & roadmap

**Done:** V1 — all three workflows end-to-end with durable HITL, demo mode, and
per-client GCP deploys. V2 — the Google ADK engine, side-by-side with LangGraph
and CI-proven equivalent (ADR-0004). Live-integration proofs for **all three**
external systems: the MLS adapter runs against a live RESO Web API feed (sparse
fields, fractional prices, vendor path casing — found and fixed); the voice path
is proven end-to-end with real phone calls (assistant hardened over five live
calls — ADR-0005); and the CRM adapter runs against a live FollowUpBoss account,
which surfaced and fixed a real `POST /calls` incompatibility (phone + direction +
a fixed outcome vocabulary the stub had accepted too loosely). LLM-backed feedback
extraction shipped behind `ExtractionPort` — a Claude Sonnet 4.6 adapter selected
per-client, deterministic default otherwise (ADR-0006), validated against the five
real call transcripts. Operator authentication shipped behind an `IdentityVerifier`
port — a deployment offers Google OIDC and/or magic-link email login (selectable per
client), gated by a shared email allowlist, with a demo operator default so demo mode
stays login-free (ADR-0007, ADR-0008); magic-link delivery goes through any SMTP
provider via an `EmailSender` adapter. Role-based access control followed
(`viewer`/`operator`/`admin`, ADR-0009): roles are assigned from config and carried in
the session, `require_role` gates the privilege-sensitive routes (admins decide
approvals, operators also start workflows and place calls, viewers read), and the
React app hides controls a role can't use — opt-in, so a deploy without role config
keeps a flat operator list. An action audit ledger records every write that crosses the
MCP boundary as a durable, secret-redacted `MutationRecord` (success or failure) at a
single port-decorator seam both engines share, linked to its approval when gated and
browsable per workflow run (ADR-0010). Those same writes are idempotent (ADR-0011): a
second decorator on the seam dedupes by `(workflow run, tool, args)`, so a retried or
resumed workflow performs each external side effect — a CRM task, an outbound call — at
most once and returns the original result. The listing→transaction handoff is built:
an operator opens an escrow via `POST /transactions`, which validates the dates and
generates a milestone timeline from a per-client template before persisting it, and the
`transaction_coordination` cron then drives it on either engine. Opening is idempotent
per listing (a same-terms repeat returns the existing transaction; different terms 409).

**Next, in rough order — each lands when a demo- or client-path justifies it,
never speculatively:**

- **Session longevity:** session JWTs (8h) and Google ID tokens just re-prompt on
  expiry; a refresh flow lands when an operator path needs longer sessions.
- **Demo recording:** a 60–90s screen capture of the docs/DEMO.md path.
- **Loosen the `google-adk` pin** once its invocation-resumability API leaves
  experimental status (tracked in ADR-0004).
- **Documented-but-dormant:** MCP servers as separate Cloud Run services, and
  caching (revisit triggers in ADR-0001).

## License

Apache-2.0 — see [LICENSE](LICENSE).
