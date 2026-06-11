# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 0** — repo guardrails and runnable skeleton. The architecture:
> LangGraph orchestration over an MCP integration boundary (mock RESO Web API MLS,
> FollowUpBoss, Vapi), a framework-free hexagonal core, durable HITL via Postgres
> checkpointing, and per-client GCP deploys via Terraform. Full architecture docs land
> as the build progresses.

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
