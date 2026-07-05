# ADR-0004: LangGraph and ADK run side-by-side behind one engine seam

**Status:** Superseded by [ADR-0019](ADR-0019-one-orchestrator-langgraph.md) (2026-07-05) · **Date:** 2026-06-11

> **Superseded.** brokerops committed to a single orchestrator — LangGraph — and
> removed the ADK lane; the engine seam this ADR introduced is retained (see
> ADR-0019). This record stands as the history of why the second engine existed and
> what the port proved.

## Context

The V1 architecture enforced a portability contract: nodes contain no business
logic, all external calls go through ports, all HITL passes through
`ApprovalRequest` rows and one resume endpoint, and workflow state schemas are
plain Pydantic in `core/`. The stated V2 plan was a hard swap —
`orchestration/langgraph/` → `orchestration/adk/`, nothing else changes.

When V2 arrived, two things argued against deleting the LangGraph side:

1. **The dual-framework story is the point.** A repo that *says* its orchestrator
   is swappable is a claim; a repo where two orchestrators pass the same e2e gate
   from the same commit is a proof. Keeping both makes the portability contract
   continuously verified instead of historically true.
2. **ADK's resumability is experimental.** The ADK port runs each workflow as a
   deterministic `Workflow` of `FunctionNode`s — no `LlmAgent`, so no model
   credentials — and pauses via `RequestInput` interrupts resumed by invocation
   id. That invocation-resume machinery is explicitly marked experimental
   upstream (it warns at startup). Betting the only engine on it would couple
   demo stability to a moving API.

## Decision

Both engines ship, selected at startup by `ORCHESTRATOR=langgraph|adk`
(default: `langgraph`).

1. The API depends on a `WorkflowEngine` protocol (`start()` / `decide()`)
   only. The shared vocabulary — workflow names, `WorkflowRunResult`, the
   terminal-state rule — lives framework-free in `core/services/workflow_runs.py`.
2. Each orchestration package owns its engine and wiring:
   `brokerops_langgraph.engine` (graphs + Postgres checkpointer) and
   `brokerops_adk.engine` (workflows + ADK session service). The ADK session id
   doubles as the approval's `graph_thread_id`; the paused invocation and
   interrupt id are recovered from the session event log on resume, so the
   `ApprovalRequest` schema, API surface, and frontend are identical under
   either engine.
3. `google-adk` is pinned to its proven minor version (`~=2.2.0`) until the
   resumability API stabilizes.
4. CI runs `scripts/e2e_demo_check.sh` — unchanged — as a
   `{langgraph, adk}` matrix, plus framework-specific restart-survival proofs
   (LangGraph checkpointer; ADK `DatabaseSessionService`) against Postgres.

## Consequences

- (+) The portability contract is enforced by CI, not asserted by docs: both
  engines must pass the same scenario suites, restart proofs, and demo path on
  every push.
- (+) Demo deployments stay on the battle-tested default while the ADK side
  matures; flipping a client is an env-var change.
- (+) Zero-credential demo survives the port: the ADK workflows use no
  `LlmAgent`, so neither engine needs a model key.
- (−) Two orchestration dependencies in the API image and two engines to keep
  green; the cost is bounded by the thin-node rule (engines contain run/pause
  /resume mechanics, never business logic).
- (−) The ADK pin must be revisited deliberately; loosening it is gated on the
  resumability API losing its experimental label.
