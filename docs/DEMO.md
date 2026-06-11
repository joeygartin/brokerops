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

1. Open the frontend → **Listings** tab. Twelve listings from the mock RESO MLS.
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
(The engine behind it is LangGraph by default; rerun the stack with
`ORCHESTRATOR=adk make demo` and the identical path runs on Google ADK.)

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
   in a real deploy. The banner reports: 1 escalation waiting, reminders sent, a
   call intent queued.
3. **Approvals** tab → the escalation card shows the overdue milestone. **Approve**
   it → an `URGENT:` task lands in the CRM and the milestone's escalation level
   ratchets (visible back on the Transactions tab).
4. Click **Run milestone check** again — note it *skips* the transaction rather
   than stacking duplicate escalations while one is pending… and since the
   milestone is still overdue, a fresh escalation appears at the next level.

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
transcript syncs feedback + a CRM note automatically, with no human gate.

## 4. Tear down

```bash
docker compose down          # add -v to also drop the database volume
```

## Scripted verification

The same path, as assertions (used by CI's e2e job):

```bash
make demo && scripts/e2e_demo_check.sh
```
