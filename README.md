# brokerops

[![ci](https://github.com/joeygartin/brokerops/actions/workflows/ci.yml/badge.svg)](https://github.com/joeygartin/brokerops/actions/workflows/ci.yml)

**AI-powered backoffice for real estate brokerages** — listing-to-contract
marketing, deadline-driven transaction coordination, and voice follow-up, with a
human approval gate in front of every consequential action.

<!-- TODO(joey): 60–90s screen recording of docs/DEMO.md goes here -->

Three LangGraph workflows over an MCP tool boundary, a framework-free domain core,
durable human-in-the-loop (Postgres-checkpointed — approvals survive restarts and
deploys), and per-client GCP deploys via Terraform.

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

- **`core/`** is plain Python + Pydantic — no LangGraph, no FastAPI, no SDKs.
  Workflow nodes are thin; business rules (marketing drafts, milestone date math,
  transcript extraction — including a spoken price-range parser) live in core
  services and are unit-tested in isolation.
- **Every integration is three things:** an adapter implementing a core `Protocol`
  port, a recorded-shape stub, and an MCP server (`uv run mcp-server-mls-reso`,
  `…-followupboss`, `…-vapi`). Swapping the mock MLS for a live RESO feed is a
  base-URL + auth change, pinned by contract tests.
- **Every human gate is the same spine:** `interrupt()` → an `ApprovalRequest` row →
  the Approval Inbox → one resume endpoint. That uniformity is also what keeps the
  orchestrator swappable.

## Development

```bash
uv sync --all-packages   # install workspace deps
make test                # ~95 tests (contract, graph, API flow, restart-survival)
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
integrations/            # mls_reso · followupboss · vapi — adapter + stub + MCP server each
orchestration/langgraph/ # the three graphs + Postgres checkpointer
api/                     # FastAPI: routes, webhooks, cron, workflow engine, Alembic
frontend/                # React + Vite: Listings, Transactions, Approval Inbox
infra/                   # Terraform per-client module + bootstrap
docs/                    # ARCHITECTURE.md · DEMO.md · ADRs/
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
