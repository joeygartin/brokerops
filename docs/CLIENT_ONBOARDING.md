# Client onboarding & checklist intake

What to gather from a brokerage before deploying their instance, and exactly where
each answer lands in the system. This is the **intake sheet** — fill one out per
client, then provision per `README.md → Deploying to GCP`.

> **Current customization model (V1 onboarding): hand-coded per client.** The
> integration/auth layer is config (`tfvars` + Secret Manager). The *business
> rules* — listing task list, marketing copy, escrow milestone timeline,
> escalation cadence — currently live as code in `core/services/`. Onboarding
> client #1 means editing those services. After the first real deployment we
> extract these into a per-client **configuration layer** so subsequent clients
> are config-only. Capture everything below regardless; it's the same data either
> way, and it's what the config schema will be designed from.

Legend for each item: **[config]** = set in `infra/clients/<client>.tfvars` or a
secret; **[code]** = currently hand-edited in `core/`; **[unbuilt]** = no
mechanism yet, must be built for this client.

---

## 1. Brokerage identity & operators

| Capture | Lands in |
|---|---|
| Client short name (slug, e.g. `acme`) | `client_name`, all secret/resource names **[config]** |
| GCP project id + region | `project_id`, `region` **[config]** |
| Who logs in, and how (Google Workspace SSO vs. email magic-link) | `auth_methods`, `google_oidc_client_id` **[config]** |
| Allowed sign-in domain and/or explicit emails | `auth_allowed_domain`, `auth_allowed_emails` **[config]** |
| **Roles:** who is admin (decides approvals) / operator (starts work, places calls) / viewer (read-only) | `auth_admin_emails`, `auth_viewer_emails`, etc. (ADR-0009) **[config]** |
| Public URL for the frontend (for magic-link emails) | `public_base_url` **[config]** |

## 2. Integration accounts (one set of credentials per client — never shared)

| System | Capture | Lands in |
|---|---|---|
| **MLS (RESO Web API)** | OData service-root URL + bearer token | `reso_base_url` **[config]** + `reso-auth-token` secret |
| **CRM (FollowUpBoss)** | API key + base URL | `fub_base_url` **[config]** + `fub-api-key` secret |
| **Voice (Vapi)** | API key, assistant id, phone-number id, webhook secret | `vapi_*` vars **[config]** + `vapi-api-key`/`vapi-webhook-secret` secrets |
| **Office files (Google Drive)** | Auth mode + folder convention — see §2a | `FILES_PROVIDER` **[config]** + drive-credentials secret |
| **LLM feedback extraction** (optional) | Use Claude vs. deterministic? model id? | `enable_llm_extraction`, `llm_model` **[config]** + `llm-api-key` secret |
| **Email delivery** (for magic-link) | SMTP host/port/from/username | `smtp_*` **[config]** + `smtp-password` secret — for AWS SES, `scripts/setup_ses.sh <client> <domain>` automates the identity + IAM user + password push (prints the DKIM records and deploy `-var`s) |
| **Outbound business email (SES)** (client-facing comms, BOP-016) | Provider (`ses`) + the business-comms **sending identity** (from address) — see §2b | `EMAIL_PROVIDER`, `SES_REGION`, `SES_ACCESS_KEY_ID`, `SES_FROM_ADDRESS` **[config]** + `ses-secret-access-key` secret (pushed by `setup_ses.sh`) |
| **Outbound business email (SendGrid)** | API key + authenticated sending domain + from-address — see §2c | `EMAIL_PROVIDER=sendgrid`, `SENDGRID_FROM_EMAIL` **[config]** + `sendgrid-api-key` secret |
| **SMS (Twilio)** | Dedicated Twilio account SID + auth token, Messaging Service SID (or from-number), **A2P 10DLC brand/campaign registration status** | `SMS_PROVIDER`/`TWILIO_*` **[config]** + auth-token secret — registration is a MANUAL per-client gate with real lead time: run `docs/A2P_10DLC_ONBOARDING.md` first (`scripts/setup_twilio_sms.sh <client>` wraps the scriptable rim) |

If a system isn't ready, leave it on the bundled stub (`*_base_url = "internal"`)
and turn it on later.

### 2a. Office files — Google Drive auth mode (BOP-021)

Transaction documents (purchase agreement, disclosures, inspection reports)
live in the brokerage's own Drive; brokerops stores **pointers only** (the
`documents` table holds metadata, never bytes) and reads/uploads through the
`FilesPort`. Selection is the explicit `FILES_PROVIDER` env: unset/`stub` runs
the bundled in-memory Drive double (the demo default — zero credentials);
`google_drive` talks to the real API and **fails loud at startup without
credentials** — it never silently falls back to the stub.

Capture at intake:

| Capture | Lands in |
|---|---|
| **Auth mode.** Recommended: a **per-client service account** — create it in the client's GCP project, enable the Drive API, and have the brokerage *share their transactions folder (or Shared Drive) with the service-account email*. No user consent flow, no token expiry babysitting. | Service-account JSON → Secret Manager (e.g. `drive-credentials`), mounted into the container; path in `GOOGLE_DRIVE_CREDENTIALS_FILE` **[config]** |
| **OAuth alternative** — for a brokerage that won't share folders with a service account: a Workspace admin grants the app access (domain-wide delegation) or an operator completes a one-time consent; the resulting refresh token would live in Secret Manager the same way. | **[unbuilt]** — the adapter currently loads service-account credentials only |
| **Folder convention** — where the paperwork for one transaction lives. brokerops' convention (and the stub's seed shape): **one folder named after the listing key**. If the brokerage organizes differently (per-address, per-client), capture the mapping. | Adapter folder lookup (name-based); custom mapping **[unbuilt]** |
| **Root transactions folder** — the ONE Drive folder (or Shared-Drive folder) all listing-key folders live under; capture its folder id (from the folder URL). Anchoring lookup here keeps a same-named folder elsewhere in the service account's corpus from capturing files; duplicate names still resolve deterministically (oldest wins) and are logged. | `GOOGLE_DRIVE_ROOT_FOLDER_ID` **[config]** |
| **Upload policy** — may brokerops write new files into the folder (the upload path), or attach-existing only? Every upload is recorded in the action audit-ledger. V1 uploads are **text-only and bounded** (~1M chars, under Drive's ~5 MB simple-upload limit); binaries/scans go into Drive directly and get attached by file id. | Route stays enabled; policy is operational **[config-ish]** |

Never put the service-account JSON in the repo or a tfvars file — Secret
Manager only, like every other integration credential.

### 2b. Outbound business email — the SES sending identity (BOP-016)

Client-facing comms (showing follow-ups, milestone reminders) go out through
the `EmailPort` channel (`EMAIL_PROVIDER=ses`), which is deliberately separate
from magic-link delivery (ADR-0015): different blast radius, its own config.
Unset, the zero-credential stub runs; `ses` with missing config **fails loud at
startup** — never a silent downgrade to the stub.

Capture at intake:

| Capture | Lands in |
|---|---|
| **Sending identity** — the from address clients will see (e.g. `updates@brokerops.acme.com`). Recommended: the *same domain identity* as the auth-delivery (magic-link) one, with a different from-address — one `setup_ses.sh` run provisions both channels. If the brokerage wants a **different domain** for business comms, run `scripts/setup_ses.sh <client> <comms-domain>` again — IAM send policies are per-domain and accumulate, so the second run does not revoke the first. | `SES_FROM_ADDRESS` **[config]** |
| **DKIM/SPF DNS records** for the sending domain — `setup_ses.sh` prints the EasyDKIM CNAMEs + DMARC TXT; for SPF alignment add the optional custom MAIL FROM records it prints (MX + SPF TXT on `mail.<domain>`). Records go in MANUALLY at the client's DNS provider. | client DNS **[manual]** |
| **Region + credentials** — the script mints a send-only IAM key and pushes the secret access key to Secret Manager (`brokerops-<client>-ses-secret-access-key`); the key id and region are non-secret deploy vars. | `SES_REGION`, `SES_ACCESS_KEY_ID` **[config]** + `ses-secret-access-key` secret |
| **Sandbox exit** — a fresh SES account only delivers to verified identities and has minimal quotas. Request production access (SES console → Account dashboard) *before* the first real client send; approval can take ~24h. | AWS account **[manual]** |

### 2c. Outbound business email — SendGrid domain authentication (BOP-017)

Client-facing email (showing follow-ups, milestone reminders — the `EmailPort`
channel, ADR-0015; **not** the magic-link SMTP above) sends through SendGrid
when `EMAIL_PROVIDER=sendgrid`. Selection is explicit: the API key and the
from-address are both required and **fail loud at startup** when missing or a
placeholder — never a silent fallback to the stub.

Deliverability lives or dies on **domain authentication**: without it,
client-facing mail lands in spam or is rejected outright. Do this before the
first real send, in the *client's* SendGrid account (per-client credentials,
never shared):

| Capture / do | Lands in |
|---|---|
| **Domain authentication (DKIM + SPF).** In SendGrid: Settings → Sender Authentication → Authenticate Your Domain. SendGrid issues 3 CNAME records (they cover DKIM signing and SPF alignment via the return-path); the brokerage adds them at their DNS host, then verify in SendGrid. Automated security (rotating DKIM keys via CNAME) is the default — keep it. | Client's DNS; verified state in their SendGrid account |
| **DMARC (recommended).** A `_dmarc` TXT record (start at `p=none; rua=…`) so the brokerage can see who sends as their domain; tighten once clean. | Client's DNS |
| **From-address** on the authenticated domain (e.g. `updates@yourbrokerage.com`). A free-mail or unauthenticated from-address will fail DMARC alignment. | `SENDGRID_FROM_EMAIL` **[config]** |
| **API key** — create a restricted key with the **Mail Send scope only** (never Full Access). | `sendgrid-api-key` → Secret Manager, surfaced to the container as `SENDGRID_API_KEY`; never the repo or a tfvars file |

Sends flow through the same seam as every external write — audited
(ADR-0010), deduped (ADR-0011), tenant-scoped (ADR-0012) — and every message
is reviewable in the `/messages` history.

## 3. Listing intake checklist ("taking a listing")

How the brokerage works a *new listing* once it's live. Drives the
`listing_to_contract` workflow.

| Capture | Lands in |
|---|---|
| The exact task list created when a listing is approved for marketing (e.g. "publish to portals", "order photography", "install sign", "schedule open house", "mail just-listed cards") | `core/services/followup_rules.py → plan_marketing_tasks()` **[code → config later]** |
| Conditional tasks and their triggers (e.g. luxury list above $X, land vs. residential differences) | same function — today only a hardcoded `>= $750k` luxury rule **[code]** |
| Marketing channels they actually publish to | `core/services/marketing.py → DEFAULT_CHANNELS` **[code]** |
| Marketing copy tone / required fields / disclaimers | `marketing.py → draft_marketing()` (or the LLM drafter prompt) **[code]** |
| Default due-date window for these tasks | `listing_to_contract` `TASK_DUE_DAYS` (currently 2) **[code]** |
| Which steps need human approval (today: the marketing draft) | workflow HITL gate — usually leave as-is |

## 4. Transaction / escrow checklist (the milestone timeline)

The heart of escrow management, and the **biggest build item** — see §6. Capture
the brokerage's standard timeline from contract acceptance to close.

For **each milestone** in their process:

- **Name** (e.g. "Inspection contingency", "Appraisal", "Loan approval", "Final
  walkthrough", "Closing").
- **Type** — maps to `MilestoneType` (`inspection`/`appraisal`/`financing`/`closing`/`custom`).
- **Due-date rule** — how the date is computed: *N days after contract date*, or
  *N days before close date*, or a fixed calendar date. (This is the per-client
  math the template engine must encode.)
- **Owner** — who's responsible (agent / lender / escrow / TC).
- **Escalation-worthy?** — does an overdue one trigger the HITL escalation gate
  and an URGENT CRM task, or just a reminder?

Also capture the **cadence rules** (today these are global constants, will become
per-client):

| Capture | Lands in |
|---|---|
| "Due soon" lead time (how many days out a reminder fires) | `core/services/milestone_engine.py → DUE_SOON_DAYS` (currently 3) **[code]** |
| Escalation behavior for overdue milestones | `milestone_engine.py` + `transaction_coordination` workflow **[code]** |
| Daily check time (cron) | `cron_schedule` tfvar (default `0 13 * * *`) **[config]** |
| Workflow engine preference | `orchestrator` = `langgraph`/`adk` **[config]** |

## 5. Data sources & triggers (answer these first — they gate everything)

These are the questions most likely to be unbuilt for a given client:

1. **Where do listings come from?** The MLS feed (RESO) — confirmed path. Which
   listings are "theirs" (agent/office filter)?
2. **What signals a listing has gone _under contract_?** Today this is a **manual
   operator action** — `POST /transactions` opens the escrow. An *automatic*
   trigger (MLS status flip Active → Pending, or a FUB deal stage) is **[unbuilt]**
   and is the main follow-up — see §6.
3. **Where does transaction/escrow data originate?** From the operator-triggered
   open endpoint (§6), which generates the milestone timeline and persists it.
   Auto-sync from FUB/escrow software is **[unbuilt]**.
4. **Voice follow-up:** which calls, to whom, with what assistant script
   (ADR-0005)? Drives `vapi_followup`.

## 6. Listing → transaction handoff (BOP-004 — built; follow-ups noted)

The bridge from a listing going under contract to a tracked escrow **now exists**:

- **Write path** — `TransactionStore.create_transaction` persists a transaction +
  its milestones through the domain port (no longer demo-only).
- **Milestone-template service** — `core/services/milestone_schedule.py`
  `generate_milestones()` turns a contract/close date + the client's §4 timeline
  into `Milestone` rows; `DEFAULT_TIMELINE` is the hand-coded V1 timeline.
- **Operator trigger** — `POST /transactions` (operator role) validates the escrow
  dates, generates the timeline, and persists it. Idempotent per listing (a
  same-terms repeat returns the existing transaction; different terms → 409). The
  existing `transaction_coordination` cron then drives it on either engine.

**Follow-ups (not yet built):**

- an **automatic** under-contract trigger (MLS status / FUB deal stage) to replace
  the manual `POST /transactions` (§5.2),
- lifting the §3–§4 hand-coded rules (timeline, task list, marketing) into
  **per-client config** — the #2 onboarding model, after the first deployment.

## 7. Deployment, in order

1. Fill out §1–§5 above with the client.
2. Hand-code §3–§4 rules into `core/services/` for this client (V1 model).
3. Build the §6 handoff if this client manages escrows.
4. Provision: `README.md → Deploying to GCP` (bootstrap → tfvars → images →
   `make deploy` → `make secrets`).
5. Smoke-test against their real integrations before handing over.

## 8. Sign-off

- [ ] All §1–§5 captured and confirmed with the client
- [ ] Listing checklist (§3) implemented and unit-tested
- [ ] Escrow timeline (§4) + handoff (§6) implemented and unit-tested
- [ ] Integrations smoke-tested live (MLS, CRM, voice as applicable)
- [ ] Auth + roles verified with the client's real operators
- [ ] Demo seed/reset disabled — `POST /demo/seed` returns 404 (leave `enable_demo_routes` unset; it can wipe transaction data)
