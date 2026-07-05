# ADR-0016: Second CRM adapter (Sierra Interactive) and the CRMPort widening it forced

**Status:** Accepted · **Date:** 2026-07-04 · **Relates to:** ADR-0004 (two implementations prove the seam), ADR-0010 (audit ledger), ADR-0011 (idempotent writes), ADR-0014 (explicit fail-loud selectors)

> **Historical framing (see [ADR-0019](ADR-0019-one-orchestrator-langgraph.md)).** This
> ADR was written while two orchestration engines ran side by side (ADR-0004). brokerops
> has since committed to a single LangGraph engine and removed the ADK lane. The CRMPort
> widening and its proofs described below stand; where the text says a workflow runs on
> "both engines" / "LangGraph and ADK", read the ADK half as the state at this decision's
> date — it now runs on the one LangGraph engine.

## Context

`CRMPort` was live-proven against FollowUpBoss — but a port with one adapter is a
hypothesis (the ADR-0004 lesson: the `WorkflowEngine` seam only became trustworthy when
a second engine ran behind it unchanged). This ADR records the second CRM adapter and,
more importantly, **every port/model change the second vendor forced**. The widening is
the deliverable; the vendor choice matters less than the proof.

## Vendor choice: Sierra Interactive

Evaluated against the four capabilities the workflows need — contacts read, task
create, note add, call/activity log — using each vendor's **public API documentation**
as the only source (the stubs encode documented shapes, never any real account's data):

- **Sierra Interactive** (`api.sierrainteractivedev.com`): complete, public, static
  documentation with full request/response field tables, a documented error envelope,
  and explicit required/optional markings for every endpoint we need — lead get/find,
  lead create, note add, task add/update/find. One gap: phone calls are **read-only**
  in the public API (`GET /phoneCall/{id}`; no write endpoint), resolved below.
- **Lofty**: the developer portal advertises 94 endpoints including communications,
  but the API reference is a JavaScript-rendered application whose endpoint shapes
  could not be verified from the primary public documentation. A recorded-shape stub
  can't be faithfully encoded from marketing summaries.

Sierra wins on documentation quality/verifiability — which is the whole basis for a
stub-backed offline claim — and was the task's default. Note the constant in the
adapter is the **documented** host (`api.sierrainteractivedev.com`, the base URL used
throughout Sierra's own samples); a real deploy sets `SIERRA_BASE_URL` from vendor
onboarding.

## Decision — the widenings

Each item below is a mismatch resolved **at the port** (contract, models, or wiring),
not with per-adapter special-casing in workflows or services.

1. **`Contact.fub_id` → `Contact.crm_id`.** The contact id field was named after the
   first vendor; a Sierra contact has no "FUB id". The core model is now vendor-neutral:
   `crm_id` is the contact's id in whichever CRM the deploy is wired to, opaque to
   callers, meaningful only to the adapter that produced it. (Renames ripple through
   the MCP tools' JSON output and the `/contacts` route payload — both derive from the
   model, which is the point.)

2. **`create_task.due_date` is now required** (`date`, previously `date | None`).
   Sierra's `POST /leads/{lead}/task` requires `dueOn`; an undated task is not
   representable there. Every production caller already passed a due date, so the port
   contract narrows to the honest intersection instead of having one adapter invent
   dates. The FUB adapter itself still accepts `date | None` (wider-than-port is fine,
   zero behavior change); the port-level wrappers (`RecordingCRM`, `IdempotentCRM`)
   tightened with the Protocol.

3. **Contact-less tasks are anchored by deploy config, not rejected.** FUB tasks may
   be standalone; Sierra tasks are always lead-attached (`{leadIdOrEmail}` is part of
   the URL) and always assigned (`toUserId` required). The port keeps
   `contact_id: str | None` — workflows legitimately create marketing/escalation tasks
   tied to no contact — and the port contract now states: vendors with a
   contact-anchored task model attach such tasks to a deploy-configured anchor contact,
   and `CrmTask.contact_id` **always echoes the caller's value** (the anchor is
   plumbing, not semantics). Sierra config: `SIERRA_TASK_ANCHOR_LEAD_ID` (a designated
   operations lead record) + `SIERRA_TASK_ASSIGNEE_ID` (the admin user tasks are
   assigned to). Deliberately **not** widened: an `assignee` parameter — no workflow
   assigns tasks today, so assignment stays deploy config until a caller needs it.

4. **`log_call` returns "the id of the vendor record that journals the call".**
   Sierra's public API cannot write phone calls, so the Sierra adapter journals the
   call as a lead note carrying outcome + duration (`log_call` → `POST
   /leads/{lead}/note`). The port docstring now says exactly that: adapters map
   `outcome` onto the vendor's vocabulary (FUB's fixed enum) or journal it (Sierra),
   and the returned id lives in whichever record namespace the vendor offers. Callers
   never branched on the id's meaning, so no call-site changed.

5. **Vendor-minimum validation is a declared port error.** Sierra requires an email
   (and a password) to create a lead; FUB does not. The port contract declares that
   adapters raise `ValueError` for drafts below the vendor's documented minimum — and
   for write ids that cannot name a record in the vendor's namespace — so the failure
   mode is uniform and pre-HTTP. The Sierra adapter generates the required throwaway
   password itself and suppresses the registration email — lead-site login is not
   brokerops's concern.

   Port-level ids arrive from workflow state and MCP tool args, so they are untrusted
   input to a URL: the Sierra adapter validates every `{leadIdOrEmail}` path segment
   against Sierra's two documented lead addresses (numeric id, or email — URL-encoded,
   as Sierra's own docs require) before it touches a path, so a crafted "id" can never
   rewrite the request path with the `Sierra-ApiKey` header attached. Writes with any
   other value raise `ValueError`; reads keep the port's "missing → None" semantics (a
   value that can't name a lead names nothing, and no request is sent).

6. **`RecordingCRM` takes an `integration` name.** The audit-ledger wrapper hardcoded
   `"followupboss"` into every mutation record; with two vendors that stopped being
   true. The wiring now passes the selected vendor, so the ledger attributes each CRM
   write to the actual system it hit.

7. **`CRM_VENDOR` — an explicit, closed, fail-loud selector** (the
   `ORCHESTRATOR`/`EXTRACTION_BACKEND` posture, ADR-0014): `CRM_VENDOR ∈
   {followupboss, sierra}`. Unset → FollowUpBoss, so the zero-credential demo is
   unchanged. Unknown value → `RuntimeError` at wiring. `sierra` against a real base
   URL with a missing/placeholder `SIERRA_API_KEY`, `SIERRA_TASK_ASSIGNEE_ID`, or
   `SIERRA_TASK_ANCHOR_LEAD_ID` → `RuntimeError` (an explicitly selected CRM must never
   silently run a different one). `SIERRA_BASE_URL=internal` mounts the bundled stub
   in-process with no credentials — demo-posture parity with FUB.

### Non-widenings (recorded so nobody "fixes" them later)

- **No token bucket for Sierra.** FollowUpBoss documents strict API quotas, hence its
  client-side `TokenBucket`. Sierra's public documentation specifies **no rate
  limits**, so the Sierra adapter deliberately ships without one; add it only if the
  vendor documents limits.
- **The `{"success", "data"}/errorMessage` envelope and `Sierra-ApiKey` auth** are
  Sierra shapes that never leave the integration package, exactly like FUB's basic-auth
  and payload shapes. The Sierra adapter parses the envelope on every response and
  raises `SierraApiError` carrying the vendor's `errorMessage` — for non-2xx statuses
  and for the drift case of an HTTP 200 whose body says `"success": false` — so audit
  failure records keep the vendor's reason. A 404 on the *anchored* (contact-less) task
  write is re-raised naming `SIERRA_TASK_ANCHOR_LEAD_ID`, because that is a deploy
  misconfiguration, not a caller bug.
- **Workflow state keys** (e.g. `fub_task_ids`) were not renamed — they are workflow
  output vocabulary, not port surface, and renaming them is frontend/e2e churn with no
  port meaning. Candidate for a later cleanup.

## Structure

`integrations/sierra_crm/` follows the `followupboss` layout exactly: `adapter.py`
(CRMPort over documented REST shapes), `stub.py` (recorded-shape double with synthetic
seed leads, enforcing Sierra's documented required fields and failure envelope so
contract tests catch payload drift), `mcp_server.py` (the same six tools over stdio,
`uv run mcp-server-sierra`). The MCP server mirrors the api wiring's fail-loud posture:
against the real API host, `SIERRA_API_KEY` / `SIERRA_TASK_ASSIGNEE_ID` /
`SIERRA_TASK_ANCHOR_LEAD_ID` must all be explicit; the stub's seeded defaults apply
only when `SIERRA_BASE_URL` points at a stub endpoint.

## Proof

- **One conformance suite, both vendors** (`api/tests/test_crm_conformance.py`): every
  port behavior asserted identically against the FUB adapter over the FUB stub and the
  Sierra adapter over the Sierra stub — no vendor branches — plus a typed helper that
  makes mypy prove both adapters satisfy the widened Protocol.
- **All three workflows, both engines, Sierra stub, demo wiring**
  (`api/tests/test_sierra_demo_wiring.py`): listing-to-contract (HITL approve →
  tasks), transaction-coordination (reminders), Vapi follow-up (note + call log),
  each run on LangGraph and ADK over `build_crm_adapter()` under
  `CRM_VENDOR=sierra` + `SIERRA_BASE_URL=internal`, wrapped in the same
  `IdempotentCRM(RecordingCRM(…))` write seam main.py wires — writes stay audited,
  deduped, and tenant-scoped against the second vendor.
- **FUB regression**: the FollowUpBoss adapter suite is unchanged in behavior
  (`crm_id` rename aside) and stays green against the same contract.

Live-account proof remains optional and manual; stub + contract tests carry the claim.

## Consequences

- (+) `CRMPort` is now a proven seam, not a hypothesis: two adapters with materially
  different id semantics, task models, auth, and error shapes honor one contract.
- (+) The CRM a deploy runs is stateable from config alone and fails loud when
  misconfigured; audit records name the real vendor.
- (+) Core models carry no vendor vocabulary.
- (−) `create_task` callers must supply a due date (all already did).
- (−) A Sierra deploy needs two extra config values (assignee + anchor lead) before
  task writes work — surfaced at startup, not mid-workflow.
- (−) Sierra call logs live as notes; if Sierra ever documents a call-log write
  endpoint, only the adapter changes.
