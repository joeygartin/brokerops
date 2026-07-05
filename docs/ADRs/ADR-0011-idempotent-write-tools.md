# ADR-0011: Idempotent write tools (dedupe agent retries)

**Status:** Accepted · **Date:** 2026-06-23 · **Relates to:** ADR-0010 (audit ledger), ADR-0004 (dual engines)

> **Historical framing (see [ADR-0019](ADR-0019-one-orchestrator-langgraph.md)).** This
> ADR was written while two orchestration engines ran side by side (ADR-0004). brokerops
> has since committed to a single LangGraph engine and removed the ADK lane. The seam
> and property described below still hold — the idempotency decorator sits below the one
> engine, exactly as it did below two. Read "both engines" / "two engines" as the state
> at this decision's date.

## Context

Agents retry and re-plan by nature, and an orchestrator can re-run a node on resume.
A re-issued write that crosses the MCP boundary must not produce a duplicate side
effect: a second FollowUpBoss contact/note/task, a duplicate call log, or — worst —
a second outbound phone call to a client. Before this change the external writes had
**no** replay protection. The only pre-existing "perform once" mechanisms were
unrelated to write-tool replay: magic-link single-use tokens (auth, an atomic
`UPDATE … WHERE consumed_at IS NULL`) and the feedback `upsert` (an internal DB write
keyed on a deterministic `FB-<call_id>` id). Neither guards an external write.

The shaping constraint is the same as ADR-0010: two engines (LangGraph, ADK) run the
same workflows behind one `WorkflowEngine` seam and must stay mechanically
interchangeable. A dedupe mechanism living in engine code would be built and kept in
sync twice.

## Decision

Make every external write idempotent at **one engine-agnostic seam** — a decorator
around the write-capable ports, the same seam the audit ledger uses.

1. **Key convention + store port in `core` (rules #2, #3).** `idempotency_key` derives
   a stable SHA-256 over `(workflow_run_id, tool, semantic-args)`; the derivation
   lives in a `core` service, not in tools. `IdempotencyStore` is a `Protocol` with
   `begin(key)` (atomically claim) and `complete(key, result)`. The API supplies
   `SqlIdempotencyStore` (an `idempotency_keys` table, alembic `0006`) and
   `InMemoryIdempotencyStore`. The run id comes from the same `audit_scope` ContextVar
   the ledger publishes, so the two seams agree on what "this run" is.

2. **The seam is a port decorator, wrapping the recording decorator.** `IdempotentCRM`
   /`IdempotentVoice` wrap the write-capable ports, wired once in `main.py` as
   `Idempotent(Recording(adapter))`. Because idempotency is the **outer** layer, a
   deduped replay short-circuits before the recording layer runs — it performs no side
   effect and writes **no second `MutationRecord`** (the BOP-002 contract). Reads pass
   straight through. Both engines inherit this identically (rule #5); the stub adapters
   are wrapped too, so demo mode dedupes with zero credentials.

3. **The claim is the atomicity primitive.** `begin` inserts a pending row keyed by the
   idempotency key (the primary key). The first writer wins; a concurrent or replayed
   claim hits the uniqueness constraint, reads the existing row, and learns whether the
   original **completed** (return its stored result) or is still **pending**. After the
   side effect, `complete` stores the original result and flips the row to completed, so
   a later replay — even after a process restart — answers from the durable row.

4. **At-most-once, explicitly.** The dominant path (completed key) returns the original
   result with no re-execution. The narrow window where an attempt claimed the key but
   died before `complete` (a crash between the external call returning and the local
   commit) raises `ReplayInProgressError` rather than repeat the side effect or
   fabricate a result. For outbound calls and CRM writes, refusing to repeat is the
   correct safety choice; the alternative (at-least-once) risks a second phone call.

## Consequences

- A retried or resumed workflow performs each external write at most once and returns
  the original result; an unchanged scenario is safe to replay.
- The two write-boundary seams compose: ledger truth ("what happened, once") and dedupe
  ("don't do it again") share the key fact — the run id — and the same wiring point.
- The pending-window `ReplayInProgressError` is the honest boundary of cross-system
  idempotency without downstream-native idempotency keys. Closing it fully would mean
  passing an idempotency key to FollowUpBoss/Vapi so the downstream returns the original
  result; that is a per-vendor follow-up, not a core change.
- Two identical semantic writes within one run are treated as the same operation — the
  convention's intent. The current workflows never do this (task descriptions differ);
  a future workflow that genuinely needs two identical writes would vary an argument.

## Alternatives considered

- **Record-after-success (no pending row).** Simpler, but a crash between the side
  effect and the local write would re-execute on replay → duplicate. Rejected: it
  fails the at-most-once requirement in exactly the case dedupe exists for.
- **An ADK `before_tool_callback` / LangGraph node wrapper.** Same reason as ADR-0010:
  the callbacks fire only for `LlmAgent` tool calls, and these are deterministic
  zero-LLM `FunctionNode`s. A port decorator is the genuinely single seam.
- **Migrating the magic-link and feedback mechanisms onto this store.** Rejected: they
  address different concerns (auth single-use; internal deterministic-id upsert) and
  are already idempotent. Forcing a workflow-run-scoped key onto a login (which has no
  run) would be wrong. They are not parallel external-write dedupe mechanisms, so there
  is nothing to converge.
