#!/usr/bin/env bash
# Asserts the full demo path against a running compose stack (see docs/DEMO.md).
# Used locally after `make demo` and by CI's e2e job. Exits non-zero on failure.
set -euo pipefail

API="${API:-http://localhost:8000}"
FUB="${FUB:-http://localhost:8002}"
FRONTEND="${FRONTEND:-http://localhost:5173}"

say() { printf '\n==> %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; exit 1; }
jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

say "api ready"
curl -sf "${API}/healthz" >/dev/null || fail "api not responding"

say "seed demo data (reset)"
seeded=$(curl -sf -X POST "${API}/demo/seed" -H 'Content-Type: application/json' \
  -d '{"reset": true}' | jget "d['transactions']")
[ "${seeded}" = "3" ] || fail "expected 3 seeded transactions, got ${seeded}"

say "drain pending approvals left by earlier runs (seed reset keeps them)"
for approval_id in $(curl -sf "${API}/approvals" | jget "'\n'.join(a['id'] for a in d)"); do
  curl -sf -X POST "${API}/approvals/${approval_id}/decide" -H 'Content-Type: application/json' \
    -d '{"decision": "rejected", "decided_by": "e2e-drain"}' >/dev/null || true
done
[ "$(curl -sf "${API}/approvals" | jget "len(d)")" = "0" ] || fail "could not drain approvals"

say "listings served from the mock RESO MLS"
count=$(curl -sf "${API}/listings" | jget "len(d)")
[ "${count}" = "12" ] || fail "expected 12 listings, got ${count}"

say "marketing workflow: start → pending approval"
started=$(curl -sf -X POST "${API}/workflows/listing-to-contract/start" \
  -H 'Content-Type: application/json' -d '{"listing_key": "RM1001"}')
approval_id=$(echo "${started}" | jget "d['approval']['id']")
run_id=$(echo "${started}" | jget "d['thread_id']")
[ -n "${approval_id}" ] || fail "no approval created"

say "no audit records before the gate is decided"
pre_audit=$(curl -sf "${API}/audit?workflow_run_id=${run_id}" | jget "len(d)")
[ "${pre_audit}" = "0" ] || fail "expected 0 audit records pre-approval, got ${pre_audit}"

say "approve marketing → CRM tasks"
tasks=$(curl -sf -X POST "${API}/approvals/${approval_id}/decide" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "decided_by": "e2e"}' \
  | jget "len(d['workflow']['output']['fub_task_ids'])")
[ "${tasks}" -ge 3 ] || fail "expected >=3 CRM tasks, got ${tasks}"
fub_tasks=$(curl -sf "${FUB}/tasks" | jget "len(d['tasks'])")
[ "${fub_tasks}" -ge 3 ] || fail "tasks not visible in CRM stub"

say "audit ledger recorded each CRM write, linked to the approval (engine-agnostic)"
audit=$(curl -sf "${API}/audit?workflow_run_id=${run_id}")
audited=$(echo "${audit}" | jget "len(d)")
[ "${audited}" = "${tasks}" ] || fail "expected ${tasks} audit records, got ${audited}"
echo "${audit}" | jget "all(r['tool']=='create_task' and r['integration']=='followupboss' \
  and r['outcome']=='success' and r['approval_id']=='${approval_id}' for r in d)" \
  | grep -qi true || fail "audit records not linked/clean"

say "milestone cron: overdue escalates, due-soon drafts a reminder email, blocker queues a call"
cron=$(curl -sf -X POST "${API}/internal/cron/milestones")
checked=$(echo "${cron}" | jget "d['checked']")
[ "${checked}" = "3" ] || fail "cron checked ${checked}, expected 3"
pending=$(curl -sf "${API}/approvals")
esc_id=$(echo "${pending}" | jget "[a['id'] for a in d if a['kind']=='approve_escalation'][0]")
[ -n "${esc_id}" ] || fail "no escalation approval from cron"
msg_id=$(echo "${pending}" | jget "[a['id'] for a in d if a['kind']=='approve_outbound_message'][0]")
[ -n "${msg_id}" ] || fail "no drafted reminder-email approval from cron (BOP-019)"
recipient=$(echo "${pending}" | jget "[a['payload']['recipient'] for a in d if a['id']=='${msg_id}'][0]")
[ "${recipient}" = "dana.whitfield@example.test" ] || fail "reminder drafted to ${recipient}"

say "cron dedup: pending gates are skipped on rerun (escalation + drafted email)"
skipped=$(curl -sf -X POST "${API}/internal/cron/milestones" \
  | jget "d['skipped_pending_escalation']")
[ "${skipped}" -ge 2 ] || fail "expected both pending gates to be skipped, got ${skipped}"

say "approve escalation → URGENT task + level ratchet"
outcome=$(curl -sf -X POST "${API}/approvals/${esc_id}/decide" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "decided_by": "e2e"}' \
  | jget "d['workflow']['output']['outcome']")
[ "${outcome}" = "escalated" ] || fail "escalation outcome was ${outcome}"
level=$(curl -sf "${API}/transactions/TXN-1001" \
  | jget "[m for m in d['milestones'] if m['type']=='inspection'][0]['escalation_level']")
[ "${level}" -ge 1 ] || fail "escalation level not ratcheted"

say "approve drafted reminder (edited body) → stub send + outbound_messages row"
msg_run_id=$(curl -sf "${API}/approvals/${msg_id}" | jget "d['graph_thread_id']")
decided=$(curl -sf -X POST "${API}/approvals/${msg_id}/decide" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "decided_by": "e2e", "edited_payload": {"body": "Edited by e2e before send."}}')
msg_outcome=$(echo "${decided}" | jget "d['workflow']['output']['outcome']")
[ "${msg_outcome}" = "reminder_email_sent" ] || fail "reminder outcome was ${msg_outcome}"
message_id=$(echo "${decided}" | jget "d['workflow']['output']['reminder_message_id']")
message=$(curl -sf "${API}/messages/${message_id}")
[ "$(echo "${message}" | jget "d['status']")" = "sent" ] || fail "message row not sent"
[ "$(echo "${message}" | jget "d['body']")" = "Edited by e2e before send." ] \
  || fail "edited body did not ship"
[ -n "$(echo "${message}" | jget "d['provider_message_id']")" ] || fail "no provider message id"

say "the approved send is in the audit ledger, linked to its approval"
email_audit=$(curl -sf "${API}/audit?workflow_run_id=${msg_run_id}" \
  | jget "[r for r in d if r['tool']=='send_email']")
echo "${email_audit}" | grep -q "send_email" || fail "send_email not in audit ledger"
curl -sf "${API}/audit?workflow_run_id=${msg_run_id}" \
  | jget "all(r['outcome']=='success' and r['approval_id']=='${msg_id}' \
  for r in d if r['tool']=='send_email')" | grep -qi true \
  || fail "send_email audit record not linked/clean"

say "voice feedback call (hot) → webhook → extraction → hot-lead gate"
curl -sf -X POST "${API}/calls/outbound" -H 'Content-Type: application/json' \
  -d '{"listing_key": "RM1006", "contact_id": "101", "scenario": "hot"}' >/dev/null
sleep 3
hot_id=$(curl -sf "${API}/approvals" \
  | jget "[a['id'] for a in d if a['kind']=='notify_agent'][0]")
[ -n "${hot_id}" ] || fail "no hot-lead approval after call"
budget=$(curl -sf "${API}/feedback?listing_key=RM1006" \
  | jget "d[0]['structured_answers']['budget_min']")
[ "${budget}" = "450000" ] || fail "spoken budget not extracted (got ${budget})"
hot_outcome=$(curl -sf -X POST "${API}/approvals/${hot_id}/decide" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "decided_by": "e2e"}' \
  | jget "d['workflow']['output']['outcome']")
[ "${hot_outcome}" = "agent_notified" ] || fail "hot outcome was ${hot_outcome}"

say "cool call syncs, then pauses at the drafted follow-up gate (BOP-019)"
curl -sf -X POST "${API}/calls/outbound" -H 'Content-Type: application/json' \
  -d '{"listing_key": "RM1002", "contact_id": "102", "scenario": "cool"}' >/dev/null
sleep 3
sentiment=$(curl -sf "${API}/feedback?listing_key=RM1002" | jget "d[0]['sentiment']")
[ "${sentiment}" = "negative" ] || fail "cool-call sentiment was ${sentiment}"
followup_id=$(curl -sf "${API}/approvals" \
  | jget "[a['id'] for a in d if a['kind']=='approve_outbound_message'][0]")
[ -n "${followup_id}" ] || fail "no drafted follow-up approval after cool call"

say "reject drafted follow-up → no send, decision recorded"
sent_before=$(curl -sf "${API}/messages" | jget "len([m for m in d if m['status']=='sent'])")
rejected=$(curl -sf -X POST "${API}/approvals/${followup_id}/decide" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "rejected", "decided_by": "e2e"}')
[ "$(echo "${rejected}" | jget "d['workflow']['status']")" = "followup_dismissed" ] \
  || fail "rejected follow-up did not end as followup_dismissed"
followup_message_id=$(echo "${rejected}" | jget "d['workflow']['output']['followup_message_id']")
[ "$(curl -sf "${API}/messages/${followup_message_id}" | jget "d['status']")" = "rejected" ] \
  || fail "rejected message row not marked rejected"
sent_after=$(curl -sf "${API}/messages" | jget "len([m for m in d if m['status']=='sent'])")
[ "${sent_after}" = "${sent_before}" ] || fail "a rejected draft must never send"

say "frontend serves"
curl -sf "${FRONTEND}/" | grep -q "<title>brokerops</title>" || fail "frontend not serving"

printf '\nE2E DEMO PATH: ALL CHECKS PASSED\n'
