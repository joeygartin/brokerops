# ADR-0010: Action audit-ledger for agent-performed external mutations

**Status:** Accepted · **Date:** 2026-06-22 · **Relates to:** ADR-0004 (dual engines)

> **Historical framing (see [ADR-0019](ADR-0019-one-orchestrator-langgraph.md)).** This
> ADR was written while two orchestration engines ran side by side (ADR-0004). brokerops
> has since committed to a single LangGraph engine and removed the ADK lane; the
> `{langgraph, adk}` matrix no longer runs. The seam and property described below still
> hold — the audit decorator sits below the one engine, exactly as it did below two. Read
> "both engines" / the matrix as the state at this decision's date.

## Context

The system performs writes against external systems on an operator's behalf: it
creates FollowUpBoss contacts/notes/tasks, logs calls, and places outbound Vapi calls
across the MCP boundary. Today three things are recorded, none of them the action
itself: `ApprovalRequest` rows capture *decisions*, the orchestrator checkpoints
capture *workflow state*, and tracing captures *execution for debugging*. There is no
trustworthy, reviewable history of *what the system actually did to a client's
systems* — which is both a compliance/trust requirement for a brokerage backoffice and
a headline product surface ("show me everything the assistant did to my CRM").

The constraint that shapes the design is ADR-0004: two orchestration engines
(LangGraph and ADK) run the same workflows behind one `WorkflowEngine` seam, and they
must stay mechanically interchangeable. An audit mechanism that lived in engine code
would have to be built, tested, and kept in sync twice.

## Decision

Record every external write at **one engine-agnostic seam** — a recording decorator
around the write-capable ports — and persist each as a `MutationRecord`.

1. **Record type + port in `core` (rule #2).** `MutationRecord` (Pydantic) captures
   tool, integration, workflow run id, approval id (when gated), actor, secret-redacted
   args, outcome (success **or** failure), external id / error, and timestamp. The
   `AuditLog` port is a `Protocol`; the API supplies `SqlAuditLog` (a `mutation_records`
   table, alembic `0005`) and `InMemoryAuditLog`.

2. **The seam is a port decorator, not an engine hook.** Both engines reach the CRM and
   voice platform through the same port objects, wired once in `main.py`. Wrapping those
   ports there (`RecordingCRM`, `RecordingVoice`) records every write in one place that
   both engines inherit identically — exactly what architecture rule #5 ("single seam,
   both engines behave the same") asks for. We deliberately did **not** use ADK's
   `before_tool_callback`/`after_tool_callback`: those fire only for `LlmAgent` tool
   calls, and these workflows are deterministic zero-LLM `FunctionNode`s that call the
   ports directly, so the callback never fires. A port decorator is the genuinely single
   seam; it also keeps demo mode zero-credential (the stub adapters are wrapped too).

3. **Per-run context via a `ContextVar`.** The ports are long-lived and process-scoped,
   so they can't carry which run/approval/actor a write belongs to. Each engine
   publishes that context on a `ContextVar` (`audit_scope`) at its run boundary — on
   `start` (run id, workflow, initiating operator) and on `decide` (additionally the
   approval id and the approver). The decorator reads it when it emits. The ContextVar
   is inherited by any child task the engine spawns, so every write inside a run sees its
   run's context. This adds exactly one small touch per engine and no change to the
   `WorkflowEngine` protocol beyond an optional `actor` on `start`.

4. **Read surface.** `GET /audit` (optionally `?workflow_run_id=`) is open to any
   authenticated operator (viewer and up) — the trail is a review surface, not a
   privileged action (consistent with ADR-0009). A minimal React "Audit trail" tab
   browses it.

## Consequences

- **Engine parity is enforced, not asserted.** The same scenario yields equivalent
  records under both `ORCHESTRATOR` values; the `{langgraph, adk}` e2e matrix runs
  `scripts/e2e_demo_check.sh`, which now asserts the approval's CRM writes are recorded
  and linked to the approval. Unit tests cover each engine's resume path directly.
- **Durable + complete.** Records are committed rows, so the trail survives a mid-run
  restart; both success and failure are recorded, so a failed external call is visible
  rather than silently absent.
- **No secrets persisted.** Args are deep-redacted by key before write; the trail stores
  semantic arguments only.
- **Pairs with idempotency (BOP-003).** Both hook the same write boundary. When dedupe
  lands, a deduped replay should produce an audit record for the replay event but no
  second external mutation — this ledger gives that work a natural home.
- **Direct operator routes are out of scope** for now: the ledger records
  *agent-performed* workflow writes. Wrapping the operator-initiated direct routes
  (e.g. manual outbound call) behind the same decorator is a later, additive step.
