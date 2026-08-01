#!/usr/bin/env bash
# Client offboarding path (BOP-036) — export + confirm-gated destroy + registry mark.
# Thin wrapper so the documented entrypoint is a shell script; logic lives in
# scripts/offboard_client.py (unit-tested). See docs/OFFBOARDING.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
exec uv run python scripts/offboard_client.py "$@"
