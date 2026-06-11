#!/usr/bin/env bash
# Interactively push a client's integration keys to Secret Manager.
#
# Usage: scripts/push_secrets.sh <client>
#
# Values never touch the repo or terraform state — they go straight from the
# prompt to Secret Manager. Press Enter to skip a secret (its placeholder
# stays; the stub integrations don't need real keys).
set -euo pipefail

CLIENT="${1:?usage: push_secrets.sh <client>}"
TFVARS="infra/clients/${CLIENT}.tfvars"
[ -f "${TFVARS}" ] || { echo "missing ${TFVARS}"; exit 1; }
PROJECT_ID="$(grep -E '^project_id\s*=' "${TFVARS}" | head -1 | sed -E 's/^[^=]+=\s*"([^"]*)".*/\1/')"

push() {
  local name="$1" prompt="$2" value
  read -r -s -p "${prompt} (Enter to skip): " value; echo
  if [ -n "${value}" ]; then
    printf '%s' "${value}" | gcloud secrets versions add \
      "brokerops-${CLIENT}-${name}" --project="${PROJECT_ID}" --data-file=-
    echo "    pushed brokerops-${CLIENT}-${name}"
  else
    echo "    skipped ${name}"
  fi
}

push fub-api-key "FollowUpBoss API key"
push vapi-api-key "Vapi API key"
push vapi-webhook-secret "Vapi webhook secret"
push langsmith-api-key "LangSmith API key"

echo "==> done. Deploy a new revision to pick up changes: make deploy CLIENT=${CLIENT}"
