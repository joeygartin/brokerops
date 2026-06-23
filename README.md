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

```bash
git clone https://github.com/joeygartin/brokerops && cd brokerops
make demo
```

Then open <http://localhost:5173> and follow **[docs/DEMO.md](docs/DEMO.md)** — a
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

```bash
uv sync --all-packages   # install workspace deps
make test                # ~120 tests (contract, workflow x2 engines, API flow,
                         # restart-survival per engine)
ORCHESTRATOR=adk make demo   # the same demo on the ADK engine
make lint                # ruff + mypy strict
```

## Deploying a client to GCP

```bash
# one-time per GCP project
make gcp-bootstrap GCP_PROJECT=<id> GCP_REGION=us-west1 TF_STATE_BUCKET=<bucket>

# per client
cp infra/clients/_template.tfvars infra/clients/acme.tfvars   # edit (no secrets)
make gcp-images CLIENT=acme                                   # build + push images
TF_STATE_BUCKET=<bucket> make deploy CLIENT=acme              # terraform apply
make secrets CLIENT=acme                                      # push real API keys
```

Each client gets two Cloud Run services, a Cloud SQL database, Secret Manager
shells (keys pushed interactively, never through the repo or tfstate), and the
milestone Scheduler job. `infra/clients/demo.tfvars` deploys a fully self-contained
demo — the integrations run their stubs in-process, so it needs zero secrets even
in the cloud.

## Layout

```
core/                    # framework-free domain: models, services, ports
integrations/            # mls_reso · followupboss · vapi (adapter + stub + MCP server each)
                         #   · llm_extraction (LLM adapter) · google_oidc · email_smtp (operator auth)
orchestration/           # the three workflows, twice: langgraph/ (V1) + adk/ (V2)
api/                     # FastAPI: routes, webhooks, cron, workflow engine, Alembic
frontend/                # React + Vite: Listings, Transactions, Approval Inbox
infra/                   # Terraform per-client module + bootstrap
docs/                    # ARCHITECTURE.md · DEMO.md · ADRs/
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
browsable per workflow run (ADR-0010).

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
