# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 2** — the `listing_to_contract` LangGraph workflow with durable
> human-in-the-loop approval: intake → marketing draft → approval gate (pauses on a
> Postgres checkpoint, survives restarts) → task fan-out. Approval Inbox in the
> frontend; every gate is an `ApprovalRequest` row decided through one resume
> endpoint. Plus Phase 1's mock RESO Web API, framework-free core, and MCP tools.

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
