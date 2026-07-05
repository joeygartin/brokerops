# ADR-0015: EmailPort for business comms, a channel-agnostic Message, and an explicit EMAIL_PROVIDER selector

**Status:** Accepted · **Date:** 2026-07-04 · **Builds on:** ADR-0005, ADR-0008, ADR-0010, ADR-0011, ADR-0012, ADR-0014

## Context

Email exists in this system only as magic-link delivery: `EmailSender` (ADR-0008),
with console and SMTP adapters, configured by the deployment's own SMTP settings.
That port is deliberately narrow — an *auth* concern. The product now needs the
outbound **business communication** channel: client-facing email today (showing
follow-ups, milestone reminders), SMS next (BOP-018), and LLM-drafted comms behind
an approval gate after that (BOP-019/020). Business comms have a different provider
(a transactional email service, not the deployment's SMTP), different blast radius
(clients, not operators), different observability needs (a queryable history of
what was said to whom), and a different write posture (audited, idempotent,
tenant-scoped like every other external write).

## Decision

1. **A second email port, not a widened first one.** `EmailPort`
   (`core/ports/messaging.py`) is the business-comms boundary:
   `send(message) -> provider_message_id`. `EmailSender` stays exactly as it is.
   Reusing it would couple login delivery to the comms provider — a deploy could
   not point business email at SES while magic links ride the brokerage's SMTP,
   and an outage or misconfiguration in one would drag down the other. Two ports,
   two configs, two blast radii.
2. **One channel-agnostic `Message` model, one table.** `Message` (core, Pydantic,
   `extra="forbid"`) carries `channel` (`email | sms`), recipient, subject/body,
   the versioned template ref, related entities (contact / listing / transaction),
   status (`drafted | pending_approval | sent | failed`), the provider message id,
   and timestamps. It persists to a single `outbound_messages` table (migration
   0008) for all channels, so SMS (BOP-018) is a new port + adapter, not a new
   schema. `pending_approval` is reserved for the LLM-drafted flow (BOP-019/020).
   This is **domain data** — the comms history, the `call_records` precedent — not
   audit-ledger data: the ledger records the *mutation* crossing the provider
   boundary; `outbound_messages` records the *communication*. Failure detail
   accordingly lives in the ledger's `error` column, not duplicated here.
3. **Explicit, closed provider selector — the ADR-0014 posture.** `EMAIL_PROVIDER
   ∈ {stub, ses, sendgrid}`. Unset → the stub, so demo mode stays zero-credential.
   Unknown values raise at wiring time. `ses`/`sendgrid` are declared now and fail
   loud with "not yet wired" until BOP-016/017 land their adapters — naming a real
   provider must never silently downgrade to the stub. No key-presence inference,
   ever.
4. **The stub is a full integration, not a shortcut.** `integrations/email_stub/`
   follows the adapter + stub + MCP-server layout (the `vapi` precedent): a
   FastAPI double of a transactional email API that prints each send to stdout and
   returns a provider id, an `EmailPort` adapter speaking its REST shape, and an
   MCP server exposing `send_email`. The adapter's default base URL is the
   `internal` sentinel — unlike real integrations there is no external counterpart
   to default to — so `docker compose up` exercises the email flow end-to-end with
   zero credentials and zero compose changes.
5. **Sends flow through the existing write seam.** The API wires
   `IdempotentEmail(RecordingEmail(adapter))` — the same two decorators, same
   layering as CRM and voice writes: idempotency outside recording, so a deduped
   replay performs no side effect and writes no second mutation record (ADR-0010,
   ADR-0011). The message store is wrapped in `ScopedMessageStore` and the table
   carries the GUC-default + forced-RLS tenant confinement (ADR-0012). Within a
   run, the message id is *deterministic* — the same SHA-256 the idempotency seam
   derives over the send's semantic fields — so a replayed send targets the same
   `outbound_messages` row instead of inserting a sibling.
6. **Templates are versioned source in core (ADR-0005, again made literal).**
   Deterministic `$param` templates live next to the `Message` model
   (`core/models/message_templates.py`), addressed by versioned ref
   (`showing_followup:v1`) that is persisted on every message — the exact text
   that produced a sent email is always recoverable from git. Rendering is
   `string.Template.substitute`: pure, and loud on a missing parameter. LLM
   drafting is out of scope here (BOP-019/020).

### The send lifecycle (MessageSendService)

Render → persist DRAFTED → `EmailPort.send` through the seam → persist SENT with
the provider id (or FAILED, re-raising). A replay within a run short-circuits on
the already-SENT row; even without that, the idempotency decorator returns the
original provider id. A send whose first attempt is still pending raises
`ReplayInProgressError` (409 at the route) rather than double-email a client —
the ADR-0011 at-most-once posture, unchanged.

## Consequences

- (+) BOP-016/017 are pure adapter work: implement `EmailPort`, add a selector
  branch, done — the seam, history, templates, and route are all in place (the
  two-adapter proof, the way ADK proved `WorkflowEngine`).
- (+) SMS reuses everything but the port: same `Message`, same table, same
  service pattern, same seam (BOP-018).
- (+) Auth email and business email cannot take each other down; each is
  stateable from its own config alone.
- (+) Every client-facing send is audited, deduped, tenant-confined, and
  reviewable at `/messages` — before any LLM is allowed near the channel.
- (−) Two email ports to explain. The split is the point, but the names are
  close; the port docstrings carry the distinction.
- (−) A third selector env var (`EMAIL_PROVIDER` next to `ORCHESTRATOR` and
  `EXTRACTION_BACKEND`). Consistent posture keeps the cost low.
- (−) The `outbound_messages` history and the audit ledger both mention every
  send (as domain fact and as mutation record respectively); reviewers must know
  which surface answers which question.
- (−) At-most-once cuts both ways: a FAILED send is not retryable under the same
  `request_id` (its pending idempotency claim makes the replay a permanent 409);
  recovery is a new `request_id` — a new logical send. This is the ADR-0011
  posture applied to the retry-token flow, accepted deliberately over the risk
  of double-emailing a client.
