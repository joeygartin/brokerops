# ADR-0006: LLM-backed feedback extraction behind an ExtractionPort

**Status:** Accepted · **Date:** 2026-06-18 · **Builds on:** ADR-0002

## Context

ADR-0002 shipped deterministic structured extraction (`extract_feedback`) with the
Pydantic `ExtractedFeedback` model as the contract, and anticipated the LLM upgrade
as "replacing the body of one function."

Five real outbound feedback calls — captured over the live voice stack and committed
as golden fixtures (`core/tests/fixtures/showing_feedback_golden_calls.json`) — gave
the evidence to act, and showed exactly where the deterministic extractor fails on
transcribed speech:

- **Negation-blind hot signals**: "definitely not gonna write an offer" matched the
  offer-intent phrase list — a false positive that pings an agent about a buyer who
  declined.
- **Missed transcribed-numeral budgets**: "between 5 50 and 6" ($550k–$600k) didn't
  match the regex.
- **No "underpriced" vocabulary** and no place to record what the buyer wants in a
  future home.
- **Sentence-level mis-attribution**: a praised kitchen landing in `concerns` when the
  same sentence also said "too small".

## Decision

The upgrade is a **port + adapter seam**, not a literal function-body swap:

1. `ExtractionPort` (`core/ports/extraction.py`) — `async extract(transcript) ->
   ExtractedFeedback`. `core/` depends only on this Protocol.
2. `DeterministicExtractor` (core) is the **default adapter**, wrapping the existing
   keyword/pattern `extract_feedback`. Demo mode and any key-less deploy run through it.
3. `ClaudeExtractionAdapter` (`integrations/llm_extraction/`) calls **Claude Sonnet
   4.6** via structured outputs (`messages.parse` against the same `ExtractedFeedback`
   schema).
4. **Selection is wiring-time**: `build_extraction_port` returns the LLM adapter only
   when `LLM_API_KEY` is set (and not the Terraform `"unset"` placeholder); otherwise
   the deterministic default. Both engines take the port — no node logic changes.
5. **Per-client keys**: `brokerops-<client>-llm-api-key` in Secret Manager, injected
   only when `enable_llm_extraction = true` (model via `llm_model`, default Sonnet 4.6);
   `.env` locally; demo stays zero-credential.
6. **Schema extended**: `PriceOpinion` enum gains `underpriced`; `ExtractedFeedback`
   gains `desired_features` (what the buyer wants in a future home, for match-sending).

### Why a port instead of ADR-0002's "swap the function body"

A direct `anthropic` import inside `core/services/` would violate the framework-free
core rule and the "external systems reached through ports/integrations" rule, break the
zero-credential demo (extraction is on the demo path), and leave the LLM call outside
the LangSmith-traceable integration layer. The seam resolves all four. ADR-0002's
**schema-as-contract** decision stands unchanged — only how the schema is produced moved.

### Why a direct SDK adapter, not an MCP server (unlike MLS/FUB/Vapi)

Extraction is a pure transform called as a core service, not a workflow tool surface.
An MCP server would add a process and protocol with no benefit; the other integrations
are MCP because workflows invoke them as tools.

## Consequences

- (+) Demo stays zero-credential; CI is unchanged; both engines stay green through the
  same port (the deterministic adapter exercises the contract in tests).
- (+) The LLM call — and its tracing/wrapping — is isolated in `integrations/`, never
  in `core/`.
- (+) The schema now captures search criteria and `underpriced`, surfacing intent the
  deterministic extractor couldn't.
- (−) A new external dependency (`anthropic`) and per-call cost (~fractions of a cent
  per transcript on Sonnet 4.6) when enabled.
- (−) The five-transcript eval is non-deterministic and key-gated, so it is **not** a
  CI guard. The deterministic characterization tests remain the always-on guard; the
  eval is a manual quality check (`LLM_API_KEY=… uv run pytest
  integrations/llm_extraction/tests`).
