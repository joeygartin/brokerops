# ADR-0014: PydanticAI as an ExtractionPort backend, behind an explicit selector

**Status:** Accepted · **Date:** 2026-07-02 · **Builds on:** ADR-0002, ADR-0005, ADR-0006

> **Historical framing (see [ADR-0019](ADR-0019-one-orchestrator-langgraph.md)).** This
> ADR was written while two orchestration engines ran side by side (ADR-0004). brokerops
> has since committed to a single LangGraph engine and removed the ADK lane. The
> extraction backend and selector below are unchanged (PydanticAI is a port
> implementation, not orchestration — ADR-0019 reaffirms this); read "both engines" / "the
> orchestrators" and the `google-adk` pin precedent as the state at this decision's date.

## Context

ADR-0006 put LLM-backed feedback extraction behind the `ExtractionPort` Protocol with a
raw-SDK Claude adapter, selected implicitly: any real `LLM_API_KEY` flipped the wiring
from the deterministic default to the LLM path. Two pressures on that design:

1. **The adapter hand-rolls what an agent library does better.** Structured-output
   validation failure is terminal in the raw adapter (one `RuntimeError`, no retry).
   PydanticAI — an agent library whose native contract is "typed Pydantic output,
   validation errors fed back to the model and retried" — is a direct fit for exactly
   this seam, and its model-agnostic constructor removes the provider import from the
   adapter's spine.
2. **Key-presence inference is a silent selector.** With backends multiplying, "a key
   showed up, so run an LLM" makes the extraction path impossible to state from config
   alone, and a missing key silently *downgrades* a deploy that intended to run an LLM.

Considered and rejected: adopting PydanticAI's graph layer (pydantic-graph) as a third
workflow engine. It is the weakest of the available engines on this repo's one
CI-enforced hard requirement — durable HITL resume across process restart: no bundled
durable persistence (in-memory only), no `interrupt()` primitive, and a persistence API
in mid-rework. The `WorkflowEngine` seam keeps that door open at ~200 lines if the
maturity picture changes; nothing here touches orchestration.

## Decision

1. **A second LLM backend, same contract.** `integrations/pydantic_ai_extraction/`
   implements the existing `ExtractionPort` Protocol exactly — same signature, same
   `ExtractedFeedback` return — via a PydanticAI `Agent` with `output_type` set to the
   schema. Zero change to the Protocol, the nodes, the orchestrators, or `core/`; both
   engines get it for free because they call the port, never the implementation.
2. **Selection is a closed, explicit enum — and fails loud.** `EXTRACTION_BACKEND ∈
   {deterministic, llm, pydantic_ai}`:
   - **Unset (or `deterministic`)** → the deterministic extractor. Demo mode stays
     zero-credential. A present `LLM_API_KEY` alone now selects **nothing** — the
     ADR-0006 key-presence inference is retired.
   - **`llm` or `pydantic_ai` with a missing or placeholder key** → `RuntimeError` at
     wiring time. An explicitly selected backend must never silently downgrade to
     deterministic.
   - **Unknown value** → `RuntimeError` (mirrors the `ORCHESTRATOR` unknown-value guard).
3. **The prompt is shared versioned source in core.** The extraction system prompt is
   the natural-language phrasing of the `ExtractedFeedback` contract, so it moves to
   `core/services/extraction_prompt.py` next to the schema (ADR-0005's prompts-as-
   versioned-source, made literal). Both LLM backends import it; core stays
   framework-free — it's a plain string.
4. **Every run is bounded.** The adapter binds `UsageLimits(request_limit=3)` to each
   run: the initial request plus validation-retry round trips, nothing more. Extraction
   registers no tools, so the request cap is the whole exposure.
5. **Pin the dependency.** `pydantic-ai-slim[anthropic]~=2.3.0` — PydanticAI shipped two
   majors in ten months with a 3-month no-break window, so the minor is pinned (the
   `google-adk~=2.2.0` precedent).
6. **Defaults.** The PydanticAI backend defaults to `claude-sonnet-5`; the raw-SDK
   backend keeps `claude-sonnet-4-6` untouched (no migration spent on an adapter this
   backend may eventually retire). `LLM_MODEL` overrides either, as a bare Claude model
   id — the adapter constructs the provider explicitly, so no `anthropic:` prefix.
7. **Terraform names the backend.** `enable_llm_extraction = true` now also injects
   `EXTRACTION_BACKEND` (new `extraction_backend` variable, default `"llm"`), because
   the app no longer infers it. `llm_model` becomes an empty-default override so each
   backend's own default rules.

### Migration note (behavior change)

A deploy that relied on ADR-0006's implicit selection — `LLM_API_KEY` set, nothing else —
runs **deterministic** after this change until it sets `EXTRACTION_BACKEND=llm`.
Terraform deploys get the variable automatically on the next `make deploy` because app
image and module ship together; compose/.env users set it once. This is deliberate: the
one-time explicit step is the price of never again wondering which extractor a deploy is
running.

### Testing

- **Always-on, offline:** the new adapter's suite runs PydanticAI's `TestModel` with
  `ALLOW_MODEL_REQUESTS = False`, proving the seam (output_type wiring, Protocol
  conformance) with zero credentials — CI-safe like everything else.
- **Selector:** api-level tests cover all three behaviors — deterministic default,
  fail-loud on explicit-but-misconfigured, fail-closed on unknown values — including
  the regression case "key present, selector unset → deterministic".
- **Key-gated parity eval:** the same five golden calls and the same load-bearing
  assertions as the raw-SDK adapter's eval, so both LLM backends are held to one bar.

## Consequences

- (+) Validation failures self-correct instead of erroring; retry exposure is bounded
  and declared in one place.
- (+) The extraction path is now stateable from config alone, and misconfiguration is a
  startup error instead of a silent quality downgrade.
- (+) The provider is a constructor argument — pointing this backend at a different
  model family is config, not code.
- (+) One prompt, however many backends; drift between backends' instructions is now
  structurally impossible.
- (−) A new dependency tree (`pydantic-ai-slim[anthropic]`) and a fast-moving upstream —
  hence the minor pin, and the raw-SDK adapter retained as a proven fallback.
- (−) A one-time config step for deploys that used implicit key selection (above).
