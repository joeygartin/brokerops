# ADR-0020: PydanticAI as the DraftingPort LLM backend, hard-gated on egress filtering

**Status:** Accepted · **Date:** 2026-07-07 · **Builds on:** ADR-0005, ADR-0012, ADR-0014, ADR-0015

## Context

BOP-019 introduced the `DraftingPort` — the seam that produces an outbound message's text —
with a deterministic default (versioned templates rendered verbatim) and the full
draft → approve → send spine behind it. The `DRAFTING_BACKEND` selector was reserved but its
non-deterministic value was declared-but-unwired.

This ADR wires the actual LLM-drafted message. Two things make drafting different from
extraction (ADR-0014), and both shape the decision:

1. **The output leaves the building.** Extraction turns a transcript into an internal struct.
   Drafting produces text that is *sent to an external party*. The product's security posture
   is precisely that LLM output crosses to the outside world only through the same approval
   spine **and** the same DLP/egress seam (ADR-0012, BOP-012) as everything else — and that
   this is enforced in wiring, not asserted in a doc.
2. **The model must not choose the recipient.** An LLM emitting a full `DraftedMessage` could
   set `recipient` to an address it invented (or was prompt-injected into). Routing is not the
   model's job.

Following ADR-0014, the raw-SDK-vs-agent-library comparison was already made for extraction;
there is no reason to build a raw-SDK twin here. Drafting ships one LLM backend.

## Decision

1. **One LLM backend, same contract.** `integrations/pydantic_ai_drafting/` implements the
   existing `DraftingPort` Protocol exactly — same `draft(context) -> DraftedMessage` — via a
   PydanticAI `Agent` with `output_type=DraftedMessage`, mirroring `pydantic_ai_extraction`.
   Zero change to the Protocol, the nodes, the message-send spine, or `core/`.
2. **Selection is a closed, explicit enum — and fails loud.** `DRAFTING_BACKEND ∈
   {deterministic, pydantic_ai}` (the selector's reserved `llm` value is replaced by the
   concrete backend name, since there is no raw-SDK twin to disambiguate from). Unset (or
   `deterministic`) → template rendering, zero credentials; `pydantic_ai` with a missing or
   placeholder `LLM_API_KEY` → `RuntimeError`; an unknown value → `RuntimeError`. The
   `EXTRACTION_BACKEND` posture, one selector over.
3. **Outbound payloads are DLP-scrubbed on the send path — and the LLM backend is additionally
   hard-gated at wiring.** The invariant "LLM output only leaves through the DLP seam" is made
   real in two independent layers, because BOP-012's `EGRESS_FILTERED_MARKER` alone proves only
   that a port filters what it *returns to the agent* — the wrong direction for text going *out*
   to a recipient (a marker check on the outbound argument would be theatre):
   - **Primary control — outbound scrub in the send lifecycle.** `MessageSendService` runs every
     outbound message (deterministic or LLM, on both the direct and approve-then-send paths)
     through BOP-012's `scrub_payload` immediately before `port.send`, so a credential that
     leaked into generated `subject`/`body` is secret-shape-redacted before the provider — and
     the recipient — ever sees it. It runs at the OPERATOR seam tier, so the `CONTACT_PII`
     recipient is preserved (the send needs it); it is copy-on-write, so a clean message is
     unchanged and neither re-persisted nor diverged from its history row.
   - **Defense in depth — the wiring gate.** `build_drafting_port` takes the channel-port seams a
     draft egresses through and refuses `pydantic_ai` unless each carries the BOP-012 guard
     treatment (`EGRESS_FILTERED_MARKER`), so the LLM backend can never be wired onto a seam that
     skipped `guard_tool_ports`. No channels supplied, or any one unfiltered, fails closed.
     `send_approved` dispatches by the drafted row's channel, so *every* channel a draft can
     reach is a drafted-egress path: main.py guards **both** the email and SMS seams (with
     `guard_tool_ports`) and passes both, and both are registered engine tool ports covered by
     the enumeration test — so "no workflow drafts SMS in v1" is enforced by the gate, not
     trusted as a convention. Tests prove the refusal when *any* passed channel is unfiltered,
     *and* that a secret-shaped draft is redacted before the provider is called; the
     deterministic backend never reaches the gate.
4. **The model writes copy, not routing.** The adapter overlays every routing/identity field —
   channel, recipient, template_ref, and the entity refs — onto the model's output from the
   trusted `DraftContext`. The model meaningfully contributes only `subject` and `body`, so a
   generated draft can never address itself to a recipient the model invented or re-file
   against another entity. `output_type` stays `DraftedMessage` (the model still emits the
   whole shape); the overlay is the trust boundary.
5. **Nothing sends unattended.** The draft is persisted PENDING_APPROVAL and reaches an
   external party only through the BOP-019 approve-outbound-message gate — the LLM never has an
   unattended send path. The gate (#3) and the human gate are independent belts.
6. **The prompt is shared versioned source in core.** `core/services/drafting_prompt.py` holds
   the system prompt and the `DraftContext`-rendering helper next to the schema (ADR-0005 made
   literal, the `extraction_prompt.py` precedent). Core stays framework-free — plain strings.
   The render helper deliberately omits the recipient and entity ids: the model does not see
   routing data it must not set.
7. **Every run is bounded; pin the dependency.** `UsageLimits(request_limit=3)` per run (the
   initial request plus validation-retry round trips; drafting registers no tools), and
   `pydantic-ai-slim[anthropic]~=2.3.0` pinned — the ADR-0014 posture. Default model
   `claude-sonnet-5`; `LLM_MODEL` overrides, as a bare Claude id.

### Testing

- **Always-on, offline:** the adapter's suite runs PydanticAI's `TestModel` with
  `ALLOW_MODEL_REQUESTS = False` (fixture-scoped, the BOP-014 precedent), proving output_type
  wiring, Protocol conformance, and the routing-overlay trust boundary (a hostile
  `TestModel` recipient is discarded for the context's) — zero credentials.
- **Selector + gate:** api-level tests cover the deterministic default, the egress-gate refusal
  (no channel and an unfiltered channel both fail closed), a successful build behind a filtered
  channel, fail-loud on a missing key once the gate passes, and fail-closed on unknown values.
- **Key-gated eval:** skipped without `LLM_API_KEY`; a live sanity check that a real model
  drafts sendable, grounded copy and honors the overlay.

## Consequences

- (+) LLM-generated outbound text is structurally confined to the human-gated send path and is
  DLP-scrubbed before it reaches a provider — the security invariant is a real send-time
  mechanism (not a marker on the wrong direction), backed by a wiring gate that refuses to start
  the LLM backend on an unhardened seam.
- (+) The outbound scrub is unconditional (every send, not just LLM), so the deterministic path
  gains the same leak protection at no extra config — a near-no-op for controlled template copy.
- (+) The recipient and entity routing are never the model's to choose; prompt injection cannot
  redirect a draft.
- (+) The drafting path is stateable from config alone; misconfiguration is a startup error.
- (+) One prompt and one bounded-run policy, reusing the extraction backend's shape and the
  shared `LLM_API_KEY`/`LLM_MODEL` config.
- (−) `build_drafting_port` now depends on the channel seam being built first (main.py already
  builds it above) — a small wiring-order coupling, in exchange for the gate being real.
- (−) Another fast-moving upstream dependency tree — hence the minor pin.
