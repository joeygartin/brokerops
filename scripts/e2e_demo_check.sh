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

say "milestone cron: overdue escalates, others fan out"
cron=$(curl -sf -X POST "${API}/internal/cron/milestones")
checked=$(echo "${cron}" | jget "d['checked']")
[ "${checked}" = "3" ] || fail "cron checked ${checked}, expected 3"
esc_id=$(echo "${cron}" | jget "[r['approval_id'] for r in d['results'] if r['status']=='awaiting_approval'][0]")
[ -n "${esc_id}" ] || fail "no escalation approval from cron"

say "cron dedup: pending escalation is skipped on rerun"
skipped=$(curl -sf -X POST "${API}/internal/cron/milestones" \
  | jget "d['skipped_pending_escalation']")
[ "${skipped}" -ge 1 ] || fail "expected pending escalation to be skipped"

say "approve escalation → URGENT task + level ratchet"
outcome=$(curl -sf -X POST "${API}/approvals/${esc_id}/decide" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "decided_by": "e2e"}' \
  | jget "d['workflow']['output']['outcome']")
[ "${outcome}" = "escalated" ] || fail "escalation outcome was ${outcome}"
level=$(curl -sf "${API}/transactions/TXN-1001" \
  | jget "[m for m in d['milestones'] if m['type']=='inspection'][0]['escalation_level']")
[ "${level}" -ge 1 ] || fail "escalation level not ratcheted"

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

say "cool call syncs without a gate"
pending_before=$(curl -sf "${API}/approvals" | jget "len(d)")
curl -sf -X POST "${API}/calls/outbound" -H 'Content-Type: application/json' \
  -d '{"listing_key": "RM1002", "contact_id": "102", "scenario": "cool"}' >/dev/null
sleep 3
sentiment=$(curl -sf "${API}/feedback?listing_key=RM1002" | jget "d[0]['sentiment']")
[ "${sentiment}" = "negative" ] || fail "cool-call sentiment was ${sentiment}"
pending_after=$(curl -sf "${API}/approvals" | jget "len(d)")
[ "${pending_after}" = "${pending_before}" ] || fail "cool call should not create approvals"

say "frontend serves"
curl -sf "${FRONTEND}/" | grep -q "<title>brokerops</title>" || fail "frontend not serving"

printf '\nE2E DEMO PATH: ALL CHECKS PASSED\n'
