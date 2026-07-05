#!/usr/bin/env bash
# Onboard a client for outbound SMS via Twilio — the scriptable rim of the A2P
# 10DLC checklist (docs/A2P_10DLC_ONBOARDING.md). The registration itself
# (brand + campaign) is a MANUAL per-client compliance gate in the Twilio
# Console; this script only (a) reports its current status, (b) pushes the auth
# token to the client's GCP Secret Manager, and (c) prints the deploy vars.
#
# Usage:
#   TWILIO_ACCOUNT_SID=ACxxxx TWILIO_AUTH_TOKEN=... scripts/setup_twilio_sms.sh <client>
#   <client>  the tfvars key, e.g. `demo` (reads infra/clients/<client>.tfvars)
#
# Secret safety: the auth token is read from the ENVIRONMENT only — it never
# appears on a command line (curl auth is fed via `-K -` config on stdin; the
# Secret Manager push pipes it via the shell's builtin printf to
# `gcloud --data-file=-`), so it never shows in `ps`, argv, or this repo.
set -euo pipefail

CLIENT="${1:?usage: TWILIO_ACCOUNT_SID=… TWILIO_AUTH_TOKEN=… setup_twilio_sms.sh <client>}"

# --- preflight ---------------------------------------------------------------
for bin in curl jq gcloud; do
  command -v "${bin}" >/dev/null 2>&1 || { echo "missing dependency: ${bin}" >&2; exit 1; }
done
: "${TWILIO_ACCOUNT_SID:?set TWILIO_ACCOUNT_SID (the dedicated per-client account, never shared)}"
: "${TWILIO_AUTH_TOKEN:?set TWILIO_AUTH_TOKEN (from the same account)}"
case "${TWILIO_ACCOUNT_SID}" in
  AC*) ;;
  *) echo "TWILIO_ACCOUNT_SID must be an AC… account sid" >&2; exit 1 ;;
esac

TFVARS="infra/clients/${CLIENT}.tfvars"
[ -f "${TFVARS}" ] || { echo "missing ${TFVARS}" >&2; exit 1; }
PROJECT_ID="$(grep -E '^project_id[[:space:]]*=' "${TFVARS}" | head -1 \
  | sed -E 's/^[^=]+=[[:space:]]*"([^"]*)".*/\1/')"
[ -n "${PROJECT_ID}" ] || { echo "no project_id in ${TFVARS}" >&2; exit 1; }
echo "==> client ${CLIENT} (GCP project ${PROJECT_ID}), Twilio account ${TWILIO_ACCOUNT_SID}"

# Authenticated GET without the token touching argv: curl reads its config
# (including `user = …`) from stdin via `-K -`.
_twilio_get() {
  printf 'user = "%s:%s"\n' "${TWILIO_ACCOUNT_SID}" "${TWILIO_AUTH_TOKEN}" \
    | curl -sf -K - "$1"
}

# --- step 1: A2P brand status (registered manually in Trust Hub) -------------
echo
echo "--- A2P brand registrations (docs/A2P_10DLC_ONBOARDING.md §1) ---"
BRANDS="$(_twilio_get 'https://messaging.twilio.com/v1/a2p/BrandRegistrations?PageSize=20')" \
  || { echo "Twilio API auth failed — check the SID/token" >&2; exit 1; }
BRAND_COUNT="$(jq '.data | length' <<<"${BRANDS}")"
if [ "${BRAND_COUNT}" = "0" ]; then
  echo "  NONE — register the brand in Twilio Console → Trust Hub before going live."
else
  jq -r '.data[] | "  \(.sid)  status=\(.status)  \(.entity_name // "")"' <<<"${BRANDS}"
fi

# --- step 2: messaging services + their A2P campaign status ------------------
echo
echo "--- Messaging Services + campaign status (§2–§3) ---"
SERVICES="$(_twilio_get 'https://messaging.twilio.com/v1/Services?PageSize=50')"
SERVICE_SIDS="$(jq -r '.services[]?.sid // empty' <<<"${SERVICES}")"
if [ -z "${SERVICE_SIDS}" ]; then
  echo "  NONE — create a Messaging Service and attach the campaign (§3)."
else
  while read -r mg; do
    name="$(jq -r --arg mg "${mg}" '.services[] | select(.sid==$mg) | .friendly_name' <<<"${SERVICES}")"
    campaign="$(_twilio_get "https://messaging.twilio.com/v1/Services/${mg}/Compliance/Usa2p?PageSize=5" \
      | jq -r '.compliance[0].campaign_status // "NO CAMPAIGN ATTACHED"')" || campaign="lookup failed"
    echo "  ${mg}  (${name})  campaign=${campaign}"
  done <<<"${SERVICE_SIDS}"
fi

# --- step 3: push the auth token to the client's Secret Manager --------------
SECRET_NAME="brokerops-${CLIENT}-twilio-auth-token"
echo
echo "==> pushing ${SECRET_NAME} to Secret Manager (project ${PROJECT_ID})"
if ! gcloud secrets describe "${SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud secrets create "${SECRET_NAME}" --project "${PROJECT_ID}" \
    --replication-policy=automatic >/dev/null
fi
# printf is a shell builtin: the token goes straight down the pipe, never argv.
printf '%s' "${TWILIO_AUTH_TOKEN}" \
  | gcloud secrets versions add "${SECRET_NAME}" --project "${PROJECT_ID}" --data-file=- \
    >/dev/null
echo "    pushed ${SECRET_NAME}"

# --- step 4: the deploy vars (non-secret) -------------------------------------
cat <<EOF

==> status reported and secret pushed. SMS goes live ONLY when the campaign
    above is VERIFIED (the manual 10DLC gate — days to weeks). Then deploy with:

  SMS_PROVIDER=twilio
  TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
  TWILIO_AUTH_TOKEN=<from Secret Manager: ${SECRET_NAME}>
  TWILIO_MESSAGING_SERVICE_SID=<the MG… above>   # or TWILIO_FROM_NUMBER
  TWILIO_STATUS_CALLBACK_URL=https://<api-host>/webhooks/twilio-sms

(see .env.example → "Outbound SMS" and docs/A2P_10DLC_ONBOARDING.md §4–§5.)
EOF
