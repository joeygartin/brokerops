# CLAUDE.md

Guidance for AI coding agents and human contributors working in this repo.

## Project

brokerops — AI-powered backoffice for real estate brokerages. LangGraph workflows over
an MCP integration boundary (mock RESO Web API MLS, FollowUpBoss, Vapi), a
framework-free domain core, human-in-the-loop approvals, and per-client GCP deploys
via Terraform.

## Commands

- `docker compose up` — full local stack in demo mode (zero credentials required)
- `uv sync --all-packages` — install workspace dependencies
- `make test` — unit tests
- `make lint` — ruff check + format check + mypy

## Architecture rules (non-negotiable)

1. `core/` is framework-free: plain Python + Pydantic only. No LangGraph, ADK, or
   FastAPI imports — orchestration frameworks are shells around it.
2. Every external system is reached through an MCP server in `integrations/`;
   `core/` depends only on `Protocol` ports in `core/ports/`.
3. LangGraph nodes are thin: read state → call a core service or MCP tool → write
   state. Business rules live in `core/services/`, never in nodes.
4. All human-in-the-loop approvals pass through `ApprovalRequest` rows and a single
   resume endpoint.
5. No LangGraph imports outside `orchestration/langgraph/` and `api/deps.py` — this
   keeps the planned ADK port mechanical.
6. Secrets never touch the repo. `.env.example` documents every variable with fake
   values. Demo mode must run with zero credentials.

## Conventions

- Python 3.12+, uv workspaces, Ruff (line length 100), full type hints, mypy strict.
- Frontend: React 19 + Vite + TypeScript.
- Work is tracked in GitHub Issues and Milestones — one milestone per build phase.
- Every phase ships a thin vertical slice that runs end-to-end; no speculative
  abstractions.
