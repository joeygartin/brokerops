# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 4** — scheduled transaction coordination: a cron-triggered graph
> assesses every active transaction's milestones (`milestone_engine` owns the date
> math), sends reminder tasks for near deadlines, queues call intents for external
> blockers, and pauses overdue milestones at a human escalation gate — approved
> escalations create URGENT CRM tasks and ratchet the escalation level. Transaction
> Timeline in the frontend, `make demo` seeds it all. Plus Phases 1–3: mock RESO Web
> API, durable HITL (Postgres-checkpointed), FollowUpBoss integration.

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
