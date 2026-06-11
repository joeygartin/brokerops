#!/usr/bin/env bash
# Blocks commits containing terms from a local denylist file.
# The denylist (.internal-denylist, one case-insensitive term per line,
# '#' comments allowed) is intentionally NOT committed; this guard runs
# locally only and exits silently when no denylist is present.
set -euo pipefail

denylist=".internal-denylist"
[ -f "$denylist" ] || exit 0
[ "$#" -gt 0 ] || exit 0

status=0
while IFS= read -r term; do
  [ -n "$term" ] || continue
  case "$term" in '#'*) continue ;; esac
  if matches=$(grep -rniIF -- "$term" "$@" 2>/dev/null); then
    echo "BLOCKED — denylisted term in staged files:"
    echo "$matches" | sed 's/^/  /'
    status=1
  fi
done < "$denylist"
exit $status
