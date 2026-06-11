# ADR-0002: Deterministic structured extraction in V1, behind an LLM-ready schema

**Status:** Accepted · **Date:** 2026-06-11

## Context

The `vapi_followup` workflow turns a feedback-call transcript into structured data:
sentiment, liked/disliked features, price opinion, a spoken budget range, and an
offer-intent ("hot") signal. The original design called for LLM extraction validated
against a Pydantic schema.

Two constraints argued against shipping the LLM version first:

1. **Demo mode is zero-credential by requirement.** `docker compose up` must run the
   entire system — including this workflow — with no API keys. An LLM call breaks
   that for the one workflow reviewers most want to see.
2. **The V1 secret set has no LLM-provider key.** Per-client secrets are FUB, Vapi,
   and LangSmith. Adding an LLM provider is a real decision (provider choice, key
   management, per-client vs shared billing) that deserves its own moment.

## Decision

`extract_feedback(transcript) -> ExtractedFeedback` in `core/services/` is
deterministic: keyword/cue scoring for sentiment, phrase lists for offer intent,
sentence-scoped feature attribution, and a small parser for spoken price shorthand
("four fifty" → $450,000). The **Pydantic output schema is the contract** —
workflows, persistence, and CRM sync depend on `ExtractedFeedback`'s shape, never on
how it was produced.

The LLM upgrade replaces the body of one function with a schema-validated LLM call
(the same Pydantic model becomes the structured-output schema). No workflow, port,
or persistence change.

## Consequences

- (+) The full call → extraction → CRM chain demos with zero credentials.
- (+) Extraction is exactly testable (boundary cases pinned in unit tests) and free.
- (+) The swap point is one function body; the schema is already the kind LLM
  structured-output APIs consume.
- (−) Recall is limited to the patterns encoded; colloquial or oblique transcripts
  will under-extract. Acceptable for V1's recorded-transcript demo and early pilots.
- (−) Until the LLM lands, "extraction quality" is not a differentiator. Revisit
  when a real client's transcripts arrive, alongside the LLM-provider decision.
