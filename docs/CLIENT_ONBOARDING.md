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
| **LLM feedback extraction** (optional) | Use Claude vs. deterministic? model id? | `enable_llm_extraction`, `llm_model` **[config]** + `llm-api-key` secret |
| **Email delivery** (for magic-link) | SMTP host/port/from/username | `smtp_*` **[config]** + `smtp-password` secret |

If a system isn't ready, leave it on the bundled stub (`*_base_url = "internal"`)
and turn it on later.

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
2. **What signals a listing has gone _under contract_?** MLS status flip
   (Active → Pending)? A FUB deal stage? An agent action in the app? **[unbuilt]**
   — see §6. There is no automatic listing→transaction trigger today.
3. **Where does transaction/escrow data originate?** Today transactions exist only
   via demo seed. For a real client: created from the under-contract trigger, or
   entered manually, or synced from FUB/escrow software? **[unbuilt]**
4. **Voice follow-up:** which calls, to whom, with what assistant script
   (ADR-0005)? Drives `vapi_followup`.

## 6. Known gap to build for the first real client

The **listing → transaction handoff and milestone generation do not exist yet.**
`listing_to_contract` stops after publishing marketing tasks; it never creates a
`Transaction` or any `Milestone`. `transaction_coordination` only *reads* an
existing timeline. The bridge that must be built (scoped in the onboarding work):

- a **trigger** for "this listing is now under contract" (§5.2),
- a core **milestone-template service** that turns a contract/close date + the
  client's §4 timeline into `Milestone` rows,
- a **write path** to persist the new transaction + milestones (today only a
  demo-only admin insert exists).

See the engineering write-up accompanying this intake for the detailed handoff
analysis.

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
