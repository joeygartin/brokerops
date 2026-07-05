# A2P 10DLC onboarding — per-client SMS registration checklist

US carriers require every application-to-person (A2P) SMS sender on local 10-digit
numbers to be registered: a **Brand** (the legal entity texting) and a **Campaign**
(what the traffic is). Unregistered traffic is filtered or blocked outright. This
is a **manual, per-client compliance gate** — it cannot be automated away and it
has real lead time (brand vetting is usually fast; campaign review can take days
to weeks). Run this checklist per client *before* flipping `SMS_PROVIDER=twilio`
(ADR-0017); until it's done, the deploy stays on the bundled stub.

`scripts/setup_twilio_sms.sh <client>` wraps the scriptable rim of this checklist
(status checks + secret push). The registration steps themselves happen in the
Twilio Console — deliberately: they ask legal/business questions only the client
can answer, and a wrong answer is a carrier-level rejection.

## 0. Prerequisites

- [ ] The client has (or you create) a **dedicated Twilio account/subaccount** —
  one client per account, never shared (the one-set-of-credentials-per-client
  rule in `docs/CLIENT_ONBOARDING.md` §2).
- [ ] Collect the client's legal identity: **legal business name, EIN, business
  address, website, vertical**, and a contact for verification. Sole
  proprietors have a separate (lower-throughput) registration type.
- [ ] Decide the sender: a new local number bought in this account, or a number
  the client already owns (port it in first).

## 1. Brand registration (Twilio Console → Trust Hub) — MANUAL

- [ ] Trust Hub → A2P Messaging → **register the Brand** with the §0 identity.
  The EIN must match IRS records exactly — the #1 rejection cause.
- [ ] Wait for `APPROVED` (usually minutes–hours; sole prop can differ).
- [ ] Record the Brand SID (`BN…`) on the client's intake sheet.

## 2. Campaign registration — MANUAL

- [ ] Under the approved Brand, **register a Campaign**. For this system the
  honest fit is usually **Mixed** or **Customer Care** (showing follow-ups,
  escrow milestone reminders to the brokerage's own clients).
- [ ] Sample messages: use the real templates
  (`core/src/brokerops_core/models/message_templates.py` — e.g.
  `showing_followup_sms:v1`) with placeholders filled.
- [ ] Opt-in description: state how recipients consented (e.g. provided their
  number to the brokerage during a showing/transaction and agreed to receive
  updates). The brokerage must actually have this consent flow.
- [ ] Include opt-out language ("Reply STOP to opt out") in the description;
  Twilio's default STOP/HELP handling stays ON for Messaging Services.
- [ ] Wait for campaign `VERIFIED` (this is the days-to-weeks step).

## 3. Messaging Service + number — Console or `setup_twilio_sms.sh` prints the state

- [ ] Create a **Messaging Service** for the client and attach the campaign to it.
- [ ] Add the client's number(s) to the Messaging Service's sender pool.
- [ ] Record the Messaging Service SID (`MG…`) — this becomes
  `TWILIO_MESSAGING_SERVICE_SID` (preferred over a bare `TWILIO_FROM_NUMBER`
  because throughput and STOP handling ride the service).

## 4. Wire the deploy — config, no code

- [ ] Push the auth token to the client's GCP Secret Manager:
  `scripts/setup_twilio_sms.sh <client>` (reads `TWILIO_AUTH_TOKEN` from the
  environment, never argv; creates/updates the `brokerops-<client>-twilio-auth-token`
  secret).
- [ ] Set, per `.env.example → Outbound SMS`: `SMS_PROVIDER=twilio`,
  `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (from Secret Manager),
  `TWILIO_MESSAGING_SERVICE_SID` (or `TWILIO_FROM_NUMBER`), and
  `TWILIO_STATUS_CALLBACK_URL=https://<api-host>/webhooks/twilio-sms`.
  The selector fails loud at startup if any of these are missing — that's the
  ADR-0014 posture, not a bug.
- [ ] Delivery webhook: `TWILIO_STATUS_CALLBACK_URL` is sent with every message
  as `StatusCallback`, and the webhook validates Twilio's signature against that
  exact URL — it must be the public HTTPS URL, matching to the character.

## 5. Verify before handing over

- [ ] Send one real SMS through `POST /messages/send` (`channel: sms`) to a
  test handset; confirm the `outbound_messages` row reaches `delivered` via the
  webhook (not just `sent`).
- [ ] Text STOP from the handset and confirm Twilio blocks the next send
  (opt-out is carrier-mandated).
- [ ] Confirm the audit trail (`/audit`) shows the send with
  `integration: twilio_sms`.

## What this checklist is not

No automation here registers brands or campaigns. The API-driven parts of 10DLC
(Twilio's Trust Hub APIs) exist, but the answers are legal claims about the
client's business and consent practices — a human, per-client responsibility.
The code path is agent-runnable; this page is not (ADR-0017 §6).
