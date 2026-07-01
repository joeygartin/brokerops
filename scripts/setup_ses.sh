#!/usr/bin/env bash
# Onboard a client for magic-link email via AWS SES (runbook steps 1, 3, 4).
#
# Usage: scripts/setup_ses.sh <client> <domain>
#   <client>  the tfvars key, e.g. `demo`  (reads infra/clients/<client>.tfvars)
#   <domain>  the sending subdomain, e.g. brokerops.acme.com
#   AWS_REGION env selects the SES region (default us-west-2).
#
# Wraps the per-client SES onboarding: (1) create the SES domain identity with
# EasyDKIM, (3) create a send-only IAM user scoped to that identity + an access
# key, (4) derive the SES SMTP password and push it to the client's GCP Secret
# Manager. Steps stay MANUAL: adding the printed DKIM/DMARC DNS records, and the
# Terraform deploy (this script prints the exact `-var` values for it).
#
# Secret safety: the IAM secret access key and the derived SMTP password never
# touch the terminal, the repo, a command line, or a shell variable. A single
# python gatekeeper reads the secret over stdin and feeds the derived password
# straight into `gcloud ... --data-file=-` (stdin) itself — and only after it is
# verified non-empty (fail-closed — see steps 3/4 below).
set -euo pipefail

CLIENT="${1:?usage: setup_ses.sh <client> <domain>}"
DOMAIN="${2:?usage: setup_ses.sh <client> <domain>}"
REGION="${AWS_REGION:-us-west-2}"
IAM_USER="brokerops-${CLIENT}-ses-smtp"

# --- preflight -------------------------------------------------------------
for bin in aws jq gcloud python3; do
  command -v "${bin}" >/dev/null 2>&1 || { echo "missing dependency: ${bin}" >&2; exit 1; }
done

TFVARS="infra/clients/${CLIENT}.tfvars"
[ -f "${TFVARS}" ] || { echo "missing ${TFVARS}" >&2; exit 1; }
PROJECT_ID="$(grep -E '^project_id[[:space:]]*=' "${TFVARS}" | head -1 \
  | sed -E 's/^[^=]+=[[:space:]]*"([^"]*)".*/\1/')"
[ -n "${PROJECT_ID}" ] || { echo "no project_id in ${TFVARS}" >&2; exit 1; }

CALLER="$(aws sts get-caller-identity --output json)" \
  || { echo "aws sts get-caller-identity failed — check your AWS credentials" >&2; exit 1; }
ACCOUNT="$(jq -r '.Account' <<<"${CALLER}")"
echo "==> AWS account ${ACCOUNT} as $(jq -r '.Arn' <<<"${CALLER}"), region ${REGION}"
echo "==> GCP project ${PROJECT_ID} (client ${CLIENT}), domain ${DOMAIN}"

# --- step 1: SES domain identity (idempotent) ------------------------------
if aws sesv2 get-email-identity --email-identity "${DOMAIN}" --region "${REGION}" \
     >/dev/null 2>&1; then
  echo "==> SES identity ${DOMAIN} already exists"
else
  echo "==> creating SES identity ${DOMAIN} (EasyDKIM)"
  # Tolerate a concurrent create / stale read: AlreadyExistsException means the
  # identity now exists, which is the desired state — any other error is fatal.
  if ! err="$(aws sesv2 create-email-identity --email-identity "${DOMAIN}" \
        --region "${REGION}" 2>&1 >/dev/null)"; then
    case "${err}" in
      *AlreadyExistsException*) echo "==> SES identity ${DOMAIN} already exists (raced)" ;;
      *) echo "${err}" >&2; exit 1 ;;
    esac
  fi
fi

# Read the EasyDKIM tokens as JSON and require exactly 3 non-empty tokens — a
# missing/partial set (null, [], or fewer) means DKIM is not provisioned and the
# DNS guidance below would be bogus, so fail loudly rather than print garbage.
DKIM_TOKENS="$(aws sesv2 get-email-identity --email-identity "${DOMAIN}" \
  --region "${REGION}" --query 'DkimAttributes.Tokens' --output json)"
TOKEN_COUNT="$(jq '[.[]? | select(. != null and . != "")] | length' <<<"${DKIM_TOKENS}")"
if [ "${TOKEN_COUNT}" != "3" ]; then
  echo "ERROR: expected 3 EasyDKIM tokens for ${DOMAIN}, got ${TOKEN_COUNT}." >&2
  echo "       The identity may not have EasyDKIM enabled; check the SES console." >&2
  exit 1
fi

echo
echo "--- DNS records to add MANUALLY at ${DOMAIN}'s DNS provider (step 2) ---"
jq -r '.[]' <<<"${DKIM_TOKENS}" \
  | while read -r token; do
      echo "  CNAME  ${token}._domainkey.${DOMAIN}  ->  ${token}.dkim.amazonses.com"
    done
echo "  TXT    _dmarc.${DOMAIN}  ->  \"v=DMARC1; p=none;\""
echo "-----------------------------------------------------------------------"
echo

# --- step 3: send-only IAM user scoped to the identity (idempotent) ---------
IDENTITY_ARN="arn:aws:ses:${REGION}:${ACCOUNT}:identity/${DOMAIN}"
if aws iam get-user --user-name "${IAM_USER}" >/dev/null 2>&1; then
  echo "==> IAM user ${IAM_USER} already exists"
else
  echo "==> creating IAM user ${IAM_USER}"
  # Tolerate a concurrent create / stale read: EntityAlreadyExists means the user
  # now exists (the desired state) — any other error is fatal.
  if ! err="$(aws iam create-user --user-name "${IAM_USER}" 2>&1 >/dev/null)"; then
    case "${err}" in
      *EntityAlreadyExists*) echo "==> IAM user ${IAM_USER} already exists (raced)" ;;
      *) echo "${err}" >&2; exit 1 ;;
    esac
  fi
fi

echo "==> attaching send-only inline policy scoped to ${IDENTITY_ARN}"
POLICY_DOC="$(jq -nc --arg arn "${IDENTITY_ARN}" \
  '{Version:"2012-10-17",Statement:[{Effect:"Allow",Action:["ses:SendRawEmail","ses:SendEmail"],Resource:$arn}]}')"
aws iam put-user-policy --user-name "${IAM_USER}" \
  --policy-name brokerops-ses-send --policy-document "${POLICY_DOC}"

# --- steps 3/4: access key -> derived SMTP password -> Secret Manager -------
# python is the single gatekeeper: the create-access-key JSON is piped to it over
# stdin (the IAM secret never enters a shell variable, argv, terminal, or disk).
# python prints ONLY the non-secret AccessKeyId to stdout, then — fail-closed —
# invokes `gcloud secrets versions add --data-file=-` itself, feeding the derived
# password over stdin, so gcloud runs only after a verified non-empty derivation.
# The shell captures just the AccessKeyId; if the pipeline fails it decides
# "no key created" vs. "orphan key" from whether that id was printed.
echo "==> minting access key, deriving SMTP password, pushing to Secret Manager"
SECRET_NAME="brokerops-${CLIENT}-smtp-password"

set +e
ACCESS_KEY_ID="$(aws iam create-access-key --user-name "${IAM_USER}" --output json \
  | python3 -c '
import sys, json, hmac, hashlib, base64, subprocess
region, secret_name, project = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    key = json.load(sys.stdin)["AccessKey"]
except Exception:
    sys.exit(2)  # upstream create-access-key produced no usable key
# Emit the non-secret AccessKeyId FIRST so the shell can report an orphan key
# even if the gcloud push below fails.
print(key["AccessKeyId"], flush=True)
def _sign(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
sig = _sign(("AWS4" + key["SecretAccessKey"]).encode(), "11111111")
for part in (region, "ses", "aws4_request", "SendRawEmail"):
    sig = _sign(sig, part)
password = base64.b64encode(bytes([0x04]) + sig)
if not password:
    sys.exit(3)
r = subprocess.run(
    ["gcloud", "secrets", "versions", "add", secret_name,
     "--project", project, "--data-file=-"],
    input=password, stdout=subprocess.DEVNULL,
)
sys.exit(4 if r.returncode != 0 else 0)
' "${REGION}" "${SECRET_NAME}" "${PROJECT_ID}")"
RC=$?
set -e

if [ "${RC}" -ne 0 ]; then
  if [ -n "${ACCESS_KEY_ID}" ]; then
    echo "ERROR: an access key WAS created but its password was not stored." >&2
    echo "       delete the unused key to avoid an orphan:" >&2
    echo "       aws iam delete-access-key --user-name ${IAM_USER} --access-key-id ${ACCESS_KEY_ID}" >&2
  else
    echo "ERROR: could not create an access key for ${IAM_USER}." >&2
    echo "       IAM allows max 2 keys per user — delete an unused one and retry:" >&2
    echo "       aws iam list-access-keys --user-name ${IAM_USER}" >&2
  fi
  exit 1
fi
echo "    pushed ${SECRET_NAME}"

# --- step 5: the deploy vars (non-secret) ----------------------------------
cat <<EOF

==> done. Add the DNS records above, wait for DKIM to verify, then deploy with:

  -var 'smtp_host=email-smtp.${REGION}.amazonaws.com'
  -var 'smtp_port=587'
  -var 'smtp_from=no-reply@${DOMAIN}'
  -var 'smtp_username=${ACCESS_KEY_ID}'

(plus enable_auth / auth_methods=magic and the allowlist vars — see README
"Deploying to GCP" and the canonical demo deploy command.) The smtp-password
secret is already in Secret Manager; \`make deploy CLIENT=${CLIENT}\` picks it up.
EOF
