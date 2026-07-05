# ADR-0019: One orchestrator — LangGraph — behind the retained engine seam

**Status:** Accepted · **Date:** 2026-07-05 · **Supersedes:** ADR-0004

## Context

ADR-0004 kept two orchestration engines side by side — LangGraph and Google ADK —
selected at startup by `ORCHESTRATOR=langgraph|adk`, with CI running the same e2e
demo script against both on every push. The stated value there was that a second
engine passing the same gate from the same commit turns "the orchestrator is
swappable" from a claim into a continuously-proven fact.

brokerops's focus has since narrowed to shipping the product a brokerage runs, and
that reframes the second engine. For the product, running two orchestrators buys the
buyer nothing: the workflows, approvals, API surface, and durability guarantees are
identical either way. What it costs is real and recurring — a second orchestration
dependency in the api image, a second set of workflow/engine/restart suites to keep
green, a `{langgraph, adk}` CI matrix that doubles the e2e leg, and a pinned
`google-adk` (its invocation-resumability API was experimental, ADR-0004 §3) that has
to be watched and revisited. That is maintenance tax with no product return.

Before removing anything we measured the adapter-specific surface (BOP-039 requires
measure-before-delete). The finding: the ADK lane is a **pure parallel
implementation**, not a home for any feature.

- The two lanes are one-to-one by workflow: `listing_to_contract`,
  `transaction_coordination`, and `vapi_followup` exist in both
  `orchestration/langgraph/graphs/` and `orchestration/adk/workflows/`, and the ADK
  test files are explicitly parity suites ("scenario parity with the LangGraph X
  suite, on ADK").
- Everything ADK-only is orchestration *mechanism*, not product capability: an engine
  wrapper, an ADK-flavored interrupt (`RequestInput`), and an ADK session service for
  durable state. Each has a direct LangGraph counterpart (the `interrupt()` call, the
  Postgres checkpointer).
- All workflow-state schemas and all business rules already live framework-free in
  `core/` (models, services, ports); both engines are thin shells over that same core.
- Only two files outside `orchestration/adk/` reference the package — the startup
  selector in `api/main.py` and one parity test — a contained blast radius.

There was therefore **nothing to port**: the LangGraph lane already implements the
full feature set. The stop-and-split condition in BOP-039 (any ADK-only feature worth
more than a couple of turns to port) did not fire.

Why LangGraph is the one we keep:

- It is the engine with the most production miles in this repo (the V1 default, and
  the engine every live-integration proof ran on).
- The product is long-running back-office work — transaction/milestone timelines,
  compliance checklists, comms waves — paused on human approval for hours or days.
  That is exactly the shape LangGraph's durable checkpointing and interrupt/resume
  model is built for; it is the load-bearing behavior, not a nice-to-have.
- Largest ecosystem and contributor pool of the candidates, which matters the day a
  teammate or a partner's engineer inherits maintenance.

## Decision

1. **LangGraph is the sole orchestrator.** The ADK adapter package
   (`orchestration/adk/`), its tests, its `google-adk` dependency, its CI matrix leg,
   its Dockerfile copy/install, and its Terraform/compose/env plumbing are removed.

2. **The framework-free core + ports seam is retained deliberately.** Keeping one
   orchestrator does **not** mean folding orchestration concerns back into the domain.
   `core/` stays plain Python + Pydantic (architecture rule #1); the API still depends
   only on the `WorkflowEngine` protocol (`api/workflows.py`), the shared workflow
   vocabulary still lives in `core/services/workflow_runs.py`, and the LangGraph engine
   stays behind that protocol in its own package (architecture rule #5). This is the
   substrate discipline, not demonstration ceremony: it is what keeps the domain
   testable in isolation and what makes this commitment cheap to reverse. Re-adding an
   adapter later — if the ecosystem shifts — is writing one adapter against a proven
   seam, not re-plumbing the core. The ADK lane was that proof; we keep the seam and
   drop the second implementation.

3. **The `ORCHESTRATOR` selector stays, fail-loud, single-valued.** `main.py` keeps
   the startup guard (default `langgraph`; an unknown value raises), now with one
   registered engine — mirroring the closed-selector posture the rest of the config
   uses (`EXTRACTION_BACKEND`, `EMAIL_PROVIDER`; ADR-0014/0015). A stray
   `ORCHESTRATOR=adk` fails loudly at startup rather than silently doing anything. The
   redundant Terraform `orchestrator` variable (single legal value now) is dropped;
   Cloud Run gets the default.

4. **PydanticAI is unaffected.** It lives at the typed LLM-call layer — the
   `pydantic_ai_extraction` adapter behind `ExtractionPort` (ADR-0014), and the
   drafting backend (BOP-020) — which is a *port implementation*, not orchestration.
   This decision is about the workflow engine only and does not touch it.

5. **CI collapses to single-engine.** The `e2e-demo` job drops its `{langgraph, adk}`
   matrix and runs `scripts/e2e_demo_check.sh` once; the mypy leg and pytest paths drop
   the ADK package. The demo script itself is unchanged — it was always
   engine-blind, which is the proof that the demo path never depended on the adapter
   choice.

## Consequences

- (+) One orchestration dependency, one set of workflow/engine/restart suites, one
  e2e leg — less to build, test, and keep green; total CI time drops (the e2e job
  halves).
- (+) The pinned, experimental-API `google-adk` dependency and its watch-and-revisit
  burden are gone.
- (+) The seam that made the port mechanical is retained, so the door is not welded
  shut — reversing this is one adapter against an unchanged core.
- (−) The continuously-proven portability claim of ADR-0004 becomes a historical one:
  portability is now evidenced by the retained seam and the past ADK parity, not by a
  live second engine. Accepted deliberately — a product doesn't sell a second engine.
- (−) A one-time re-plumb touches api wiring, CI, Dockerfile, Terraform, and docs; the
  contained blast radius (two importing files) kept it low-risk.

ADR-0004 is superseded. Its record stands as the history of why the second engine
existed and what the port proved.
