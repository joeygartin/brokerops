# brokerops

AI-powered backoffice for real estate brokerages — listing-to-contract, transaction
coordination, and voice follow-up workflows, with human-in-the-loop approval at every
consequential step.

> **Status: Phase 5** — voice follow-up end-to-end: an outbound showing-feedback
> call's end-of-call webhook drives the `vapi_followup` graph — transcript →
> structured extraction (Pydantic-validated: sentiment, highlights/concerns, spoken
> budget-range parsing, offer-intent detection) → persisted feedback → CRM note +
> call log; hot signals pause at a notify-agent gate that creates a hot-lead task on
> approval. Demo mode's Vapi stub fires real-shaped webhooks, so the whole chain runs
> with zero credentials. Plus Phases 1–4: mock RESO Web API, durable HITL,
> FollowUpBoss integration, scheduled transaction coordination.

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
