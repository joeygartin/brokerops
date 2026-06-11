#!/usr/bin/env bash
# Build and push the api + frontend images for a client.
#
# Usage: scripts/build_push_images.sh <client>
#
# Reads project_id/region from infra/clients/<client>.tfvars. The frontend
# bundle bakes the api's deterministic Cloud Run URL at build time.
set -euo pipefail

CLIENT="${1:?usage: build_push_images.sh <client>}"
TFVARS="infra/clients/${CLIENT}.tfvars"
[ -f "${TFVARS}" ] || { echo "missing ${TFVARS}"; exit 1; }

tfvar() { grep -E "^${1}\s*=" "${TFVARS}" | head -1 | sed -E 's/^[^=]+=\s*"([^"]*)".*/\1/'; }

PROJECT_ID="$(tfvar project_id)"
REGION="$(tfvar region)"
[ "${PROJECT_ID}" != "CHANGE-ME" ] || { echo "set project_id in ${TFVARS} first"; exit 1; }

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
API_URL="https://brokerops-${CLIENT}-api-${PROJECT_NUMBER}.${REGION}.run.app"
AR="${REGION}-docker.pkg.dev/${PROJECT_ID}/brokerops"

echo "==> building api image"
docker build --platform linux/amd64 -f api/Dockerfile -t "${AR}/api:latest" .

echo "==> building frontend image (VITE_API_BASE=${API_URL})"
docker build --platform linux/amd64 -f frontend/Dockerfile.prod \
  --build-arg "VITE_API_BASE=${API_URL}" \
  -t "${AR}/frontend:latest" frontend/

echo "==> pushing"
docker push "${AR}/api:latest"
docker push "${AR}/frontend:latest"

echo "==> done:"
echo "    api_image      = \"${AR}/api:latest\""
echo "    frontend_image = \"${AR}/frontend:latest\""
