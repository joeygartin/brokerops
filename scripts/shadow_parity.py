"""Run BOP-043 shadow-parity against local actuals + snapshot files (no client writes).

  uv run python scripts/shadow_parity.py \\
    core/tests/fixtures/shadow_parity_actuals.json \\
    core/tests/fixtures/shadow_parity_snapshot.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from brokerops_core.models.shadow_parity import ShadowActualsFile, ShadowSnapshotFile
from brokerops_core.services.shadow_parity import (
    ShadowParityMismatch,
    ShadowSourceNotConfigured,
    run_shadow_parity,
    snapshot_dict,
)


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    raw.pop("_comment", None)
    return raw


def _run(actuals_path: Path, snapshot_path: Path) -> int:
    actuals = ShadowActualsFile.model_validate(_load_json(actuals_path))
    snapshot = ShadowSnapshotFile.model_validate(_load_json(snapshot_path))
    try:
        report = run_shadow_parity(actuals, snapshot_dict(snapshot))
    except ShadowSourceNotConfigured as exc:
        print(json.dumps({"verdict": "blocked", "error": str(exc)}, indent=2))
        return 2
    except ShadowParityMismatch as exc:
        print(exc.report.model_dump_json(indent=2))
        return 1
    print(report.model_dump_json(indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="BOP-043 shadow-parity (local fixtures)")
    parser.add_argument("actuals", type=Path, help="JSON closed-deal actuals file")
    parser.add_argument("snapshot", type=Path, help="JSON frozen ledger snapshot file")
    args = parser.parse_args()
    sys.exit(_run(args.actuals, args.snapshot))


if __name__ == "__main__":
    main()
