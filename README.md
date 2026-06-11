# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 3** — FollowUpBoss CRM integration: an approved marketing draft
> fans out into real CRM tasks through `CRMPort` (rate-limited FUB adapter, six MCP
> tools, contact search/sync). Demo mode runs against a bundled FUB stub with zero
> credentials; a real FUB account is a base-URL + API-key change. Plus Phase 2's
> durable HITL workflow (Postgres-checkpointed approval gate that survives restarts)
> and Phase 1's mock RESO Web API.

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
