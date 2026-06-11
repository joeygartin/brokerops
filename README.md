# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 1** — mock RESO Web API (OData subset over synthetic seed data),
> framework-free `core/` with the first domain model and MLS port, MCP tools
> (`search_listings`, `get_listing`, `get_listing_media`), and listings rendering in
> the frontend. The architecture: LangGraph orchestration over an MCP integration
> boundary (mock RESO Web API MLS, FollowUpBoss, Vapi), a hexagonal core, durable HITL
> via Postgres checkpointing, and per-client GCP deploys via Terraform.

## Quick start (demo mode — zero credentials required)

```bash
docker compose up
```

- API: <http://localhost:8000/healthz>
- Frontend: <http://localhost:5173>

## Development

```bash
uv sync --all-packages   # install workspace deps
make test                # unit tests
make lint                # ruff check + format check
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
