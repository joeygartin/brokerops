"""Run BOP-043 shadow-parity against a local actuals fixture (no client writes).

uv run python scripts/shadow_parity.py core/tests/fixtures/shadow_parity_actuals.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from brokerops_core.models.shadow_parity import ShadowActualsFile
from brokerops_core.services.shadow_parity import (
    FixtureShadowLedgerSource,
    ShadowParityMismatch,
    ShadowSourceNotConfigured,
    run_shadow_parity,
)


def _load(path: Path) -> ShadowActualsFile:
    raw = json.loads(path.read_text())
    raw.pop("_comment", None)
    return ShadowActualsFile.model_validate(raw)


async def _run(path: Path) -> int:
    actuals = _load(path)
    try:
        report = await run_shadow_parity(actuals, FixtureShadowLedgerSource())
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
    parser.add_argument("actuals", type=Path, help="JSON actuals file")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.actuals)))


if __name__ == "__main__":
    main()
