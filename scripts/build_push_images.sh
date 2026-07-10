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

tfvar() {
  grep -E "^${1}[[:space:]]*=" "${TFVARS}" | head -1 \
    | sed -E 's/^[^=]+=[[:space:]]*"([^"]*)".*/\1/'
}

PROJECT_ID="$(tfvar project_id)"
REGION="$(tfvar region)"
[ "${PROJECT_ID}" != "CHANGE-ME" ] || { echo "set project_id in ${TFVARS} first"; exit 1; }

AR="${REGION}-docker.pkg.dev/${PROJECT_ID}/brokerops"

echo "==> building api image"
docker build --platform linux/amd64 -f api/Dockerfile -t "${AR}/api:latest" .

echo "==> building frontend image (bundle calls /api; nginx proxies via API_UPSTREAM env)"
docker build --platform linux/amd64 -f frontend/Dockerfile.prod \
  -t "${AR}/frontend:latest" frontend/

echo "==> pushing"
docker push "${AR}/api:latest"
docker push "${AR}/frontend:latest"

echo "==> done — pushed ${AR}/{api,frontend}:latest"
echo "    Working-tree (unversioned) build. Deploy it with: make deploy-dev CLIENT=${CLIENT}"
echo "    For a reproducible release, tag the repo (vX.Y.Z) → cloudbuild.release.yaml"
echo "    builds version-tagged images → make deploy CLIENT=${CLIENT} VERSION=vX.Y.Z (ADR-0025)."
