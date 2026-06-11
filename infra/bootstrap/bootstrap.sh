#!/usr/bin/env bash
# One-time GCP project setup for brokerops deploys.
#
# Usage: infra/bootstrap/bootstrap.sh <project-id> <region> <state-bucket>
#
# Enables required APIs, creates the Artifact Registry repo and the Terraform
# state bucket. Run once per project, after `gcloud auth login` and creating
# the project + linking billing:
#   gcloud projects create <project-id>
#   gcloud billing projects link <project-id> --billing-account=<ACCOUNT_ID>
set -euo pipefail

PROJECT_ID="${1:?usage: bootstrap.sh <project-id> <region> <state-bucket>}"
REGION="${2:?usage: bootstrap.sh <project-id> <region> <state-bucket>}"
STATE_BUCKET="${3:?usage: bootstrap.sh <project-id> <region> <state-bucket>}"

echo "==> enabling APIs on ${PROJECT_ID}"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  --project "${PROJECT_ID}"

echo "==> creating Artifact Registry repo 'brokerops' in ${REGION}"
gcloud artifacts repositories create brokerops \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  2>/dev/null || echo "    (already exists)"

echo "==> creating Terraform state bucket gs://${STATE_BUCKET}"
gcloud storage buckets create "gs://${STATE_BUCKET}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --uniform-bucket-level-access \
  2>/dev/null || echo "    (already exists)"
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning --project="${PROJECT_ID}"

echo "==> configuring docker auth for ${REGION}-docker.pkg.dev"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo "==> bootstrap complete. Next:"
echo "    1. set project_id in infra/clients/<client>.tfvars"
echo "    2. make gcp-images CLIENT=<client>"
echo "    3. TF_STATE_BUCKET=${STATE_BUCKET} make deploy CLIENT=<client>"
