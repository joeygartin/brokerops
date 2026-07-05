# ADR-0017: SMSPort + Twilio adapter, a fail-closed delivery-status webhook, and 10DLC as a documented manual gate

> Numbering note: claimed as ADR-0017 by BOP-018; the number may shift at
> integration if a parallel-wave task (BOP-016/017/019) lands an ADR first.

**Status:** Accepted · **Date:** 2026-07-04 · **Builds on:** ADR-0010, ADR-0011, ADR-0012, ADR-0014, ADR-0015, BOP-007

## Context

ADR-0015 built the outbound business-comms channel for email and deliberately left
SMS as "a new port + adapter, not a new schema" (BOP-018). SMS is now needed —
showing follow-ups and milestone reminders where a text out-performs an email —
and mechanically it is the Vapi playbook applied to Twilio: a `Protocol` port, an
adapter + recorded-shape stub + MCP server in `integrations/`, an explicit
provider selector, and sends through the same audited/idempotent/tenant-scoped
seam. Two things are genuinely new: Twilio *pushes delivery state back* (an email
provider's "accepted" is where our email story ends today, but Twilio tells us
`delivered`/`undelivered` per message), and US A2P traffic is gated by 10DLC
brand/campaign registration — a real-world compliance step no adapter can absorb.

## Decision

1. **A second channel port, not a widened EmailPort.** `SMSPort`
   (`core/ports/messaging.py`): `send(message) -> provider_message_id`, over the
   same channel-agnostic `Message` (`channel=sms`, subject persisted empty) and
   the same `outbound_messages` table — no new schema, exactly as ADR-0015
   planned. A separate port because the channels are separately wired, separately
   decorated, and must fail independently.
2. **`integrations/twilio_sms/` is a full integration** (the vapi/email_stub
   layout): a plain-httpx adapter speaking the Twilio Messages API (form-encoded
   create, basic auth, `MessagingServiceSid` or `From`) — **no Twilio SDK**; the
   two calls we make don't earn a dependency — plus a recorded-shape stub
   (Twilio's create-response shape: `SM…` sid, `"queued"`, `error_code: null`,
   printed to stdout for compose logs) and an MCP server exposing `send_sms`
   (fail-loud config against the real API host, the sierra MCP posture).
   Failures raise `TwilioApiError` carrying the vendor envelope's `code` +
   `message` (the SierraApiError precedent) so audit failure records keep the
   reason — bad To-number vs STOP-listed recipient vs unregistered campaign —
   with an HTTP-status fallback when the body isn't Twilio's envelope.
3. **Explicit, closed selector:** `SMS_PROVIDER ∈ {stub, twilio}`, mirroring
   EMAIL_PROVIDER (ADR-0014 posture). Unset → the in-process stub over the
   `internal` sentinel (zero-credential demo). `twilio` fails loud at wiring
   without `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + a sender
   (`TWILIO_FROM_NUMBER` or `TWILIO_MESSAGING_SERVICE_SID`). No key-presence
   inference; unknown values raise.
4. **Sends flow through the existing write seam:** the API wires
   `IdempotentSMS(RecordingSMS(adapter))` — same layering, same at-most-once
   contract, same deterministic message id within a run. The seam tool is
   `send_sms`, and `channel` is a semantic send field, so the same words leaving
   by email and by SMS in one run are two writes; a re-issued SMS is one.
   `MessageSendService` gains `send_sms` over the shared render → DRAFTED → send
   → SENT/FAILED lifecycle; `POST /messages/send` takes `channel: email|sms`.
5. **A fail-closed delivery-status webhook** (`POST /webhooks/twilio-sms`, the
   BOP-007 posture). Twilio signs callbacks with the account auth token
   (HMAC-SHA1 over URL + sorted params, base64 — `X-Twilio-Signature`); we
   validate with a from-scratch implementation in the integration package,
   known-answer-pinned against the official SDK's `RequestValidator`.
   Unconfigured or placeholder (`"unset"`) token → **500 on every callback**,
   never an open endpoint; invalid signature → 401. A valid callback transitions
   the `outbound_messages` row by provider message id: `sent → delivered`
   (new `MessageStatus.DELIVERED`) or `sent → failed` (`failed`/`undelivered`).
   Transitions are **forward-only** (`STATUS_RANK`): Twilio guarantees no
   callback ordering, so a late `sent` never downgrades a `delivered` row.
   `MessageStore` gains `get_message_by_provider_id` (tenant-confined like every
   other read); unknown sids are acknowledged (200) so Twilio doesn't retry
   forever, and the status write is a domain-state update, not an external
   mutation — it does not enter the action ledger.
6. **A2P 10DLC is a documented manual gate, not automation** (the `setup_ses.sh`
   precedent): `docs/A2P_10DLC_ONBOARDING.md` is the per-client checklist
   (brand → campaign → number/Messaging-Service attach → webhook config), and
   `scripts/setup_twilio_sms.sh` wraps the scriptable rim — registration status
   checks via the Twilio API and pushing the auth token to the client's Secret
   Manager — while the registration itself stays a human step in the Twilio
   console. The code path is agent-runnable; the compliance path is not.

## Consequences

- (+) SMS inherited the entire BOP-015 machinery: history, templates, seam,
  route, review surface — the port + adapter were the only genuinely new code.
- (+) The comms history now records provider-confirmed delivery, not just
  provider acceptance — the first channel with closed-loop send state.
- (+) Demo mode sends SMS with zero credentials, and the delivery webhook is
  exercisable in compose (a demo signing token, the VAPI_WEBHOOK_SECRET pattern).
- (−) `DELIVERED` exists for SMS only until an email delivery webhook lands
  (BOP-016/017 may reuse the rank mechanism wholesale).
- (−) The signature covers the URL as Twilio saw it: deploys behind a proxy must
  pin `TWILIO_STATUS_CALLBACK_URL`, one more config knob (documented in
  `.env.example`).
- (−) A FAILED-by-callback message is terminal-ranked; a later delivered
  callback for the same sid is ignored. Twilio treats both as terminal, so this
  ambiguity is accepted.
- (−) 10DLC means SMS is never turn-key for a new client: days-to-weeks of
  registration lead time before the first real text. The checklist makes that
  explicit instead of discovering it at go-live.
