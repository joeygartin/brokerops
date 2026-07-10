# The 5-minute demo

Everything below runs locally with **zero credentials** — the MLS, CRM, and voice
platform are bundled stubs that speak the real APIs' shapes.

**Prerequisites:** Docker (with compose) and `make`. Nothing else.

## 0. Start the stack (~2 min first build)

```bash
make demo
```

This builds and starts five containers (Postgres, mock RESO MLS, FollowUpBoss stub,
Vapi stub, the api) plus the frontend, runs database migrations, and seeds three
demo transactions. When it finishes:

- **Frontend:** <http://localhost:5173>
- **API:** <http://localhost:8000> (interactive docs at `/docs`)
- **CRM stub:** <http://localhost:8002> (watch tasks land at `/tasks`)

## 1. Marketing approval — the HITL pattern (1 min)

1. Open the frontend. It lands on a role-shaped home (BOP-030) — the demo runs as a
   full admin, so you arrive at the **Approvals** inbox. Click the **Listings** tab:
   twelve listings from the mock RESO MLS.
2. On any **active** listing, click **Start marketing workflow**.
3. Switch to the **Approvals** tab. The workflow has paused at a human gate: review
   the generated marketing draft (headline, body, channels).
4. Click **Approve**. The banner reports the workflow completed and **3 CRM tasks
   created** — verify them in the CRM stub:

```bash
curl -s http://localhost:8002/tasks | python3 -m json.tool
```

**What just happened:** a workflow ran intake → draft → a HITL interrupt →
your approval resumed it → it fanned out real CRM tasks through the CRM port.
(The engine behind it is LangGraph, behind a thin `WorkflowEngine` seam that keeps
orchestration out of the domain core — ADR-0019.)

### Optional: prove the pause is durable

Start another workflow, and *before approving it*:

```bash
docker compose restart api
```

Reload the Approvals tab — the pending approval is still there (Postgres-backed
state), and approving it resumes the workflow in the new process.

## 2. Scheduled transaction coordination (1 min)

1. Go to the **Transactions** tab. Three seeded transactions with milestone
   timelines — one has an **overdue** home inspection (red), one a **due-soon**
   inspection (amber), one is **blocked** on a lender (purple).
2. Click **Run milestone check (cron)** — this is what Cloud Scheduler calls daily
   in a real deploy. The banner reports: 1 escalation waiting, reminder tasks sent
   (plus a drafted reminder email waiting), a call intent queued.
3. **Approvals** tab → the escalation card shows the overdue milestone. **Approve**
   it → an `URGENT:` task lands in the CRM and the milestone's escalation level
   ratchets (visible back on the Transactions tab).
4. The due-soon transaction produced a second card: a **drafted reminder email**
   to the listing agent. The body is **editable** — tweak it and **Approve** →
   exactly your text sends through the email provider (watch
   `docker compose logs api` for the stub's printout) and lands in the
   `outbound_messages` history and the audit ledger.
5. Click **Run milestone check** again — gates don't stack, but suppression is
   *per gate kind* and only while that kind's card is still **pending**: a
   pending escalation skips the transaction's run entirely, while a pending
   drafted email suppresses only the email tail. You decided both cards in
   steps 3–4, so this rerun produces a **fresh escalation at the next level**
   (the milestone is still overdue) *and* a **second drafted reminder email**.

## 3. Voice feedback call — webhook-driven workflow (1 min)

1. **Listings** tab → click **Feedback call** on a listing.
2. The Vapi stub "completes" the call instantly and fires a real-shaped
   end-of-call webhook back at the api. The first call uses a **hot** recorded
   transcript: the buyer wants to write an offer.
3. **Approvals** tab → a **Hot lead** card: extracted sentiment, liked features,
   and the budget range parsed from *spoken* numbers ("four fifty and five twenty
   five" → $450,000–$525,000). **Approve** → a hot-lead task lands in the CRM.
4. Inspect the structured extraction:

```bash
curl -s "http://localhost:8000/feedback?listing_key=RM1001" | python3 -m json.tool
```

Click **Feedback call** on another listing for the contrasting path: a lukewarm
transcript syncs feedback + a CRM note automatically, then drafts a follow-up
email to the toured buyer and pauses at an **outbound-message card** — edit the
draft and approve to send it, or reject and nothing ever leaves the system.

## 4. Tear down

```bash
docker compose down          # add -v to also drop the database volume
```

## Scripted verification

The same path, as assertions (used by CI's e2e job):

```bash
make demo && scripts/e2e_demo_check.sh
```

The golden path above is also **CI-enforced in a real browser**: a Playwright spec
(`frontend/e2e/golden-path.spec.ts`) drives Chromium through boot → Listings → start
marketing → approve → CRM-task confirmation, plus a transaction detail visit — on every
push to `main` (the `e2e-browser` job). To run it locally against the demo stack:

```bash
make demo                              # brings the stack up and seeds the demo data
cd frontend
npm ci                                 # install frontend deps (incl. @playwright/test)
npx playwright install chromium        # one-time browser download
npx playwright test                    # drives the golden path in Chromium
```
