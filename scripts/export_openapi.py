"""Export the API's OpenAPI spec to frontend/openapi.json, deterministically.

The Pydantic models are the single source of truth for the wire contract;
`app.openapi()` is their projection. This script pins the environment knobs
that change the mounted route surface (only ENABLE_DEMO_ROUTES today) so the
committed spec is reproducible regardless of the caller's shell, then writes
sorted, newline-terminated JSON so regeneration is byte-stable.

Run via `make generate` (or `uv run python scripts/export_openapi.py`); the
frontend's `npm run generate` turns the spec into the committed TS client, and
CI regenerates both and fails on any diff (ADR-0018).
"""

import json
import os
import sys
from pathlib import Path

# Demo routes are gated at mount time (absent from the schema when disabled).
# The demo seed surface is part of the product's demo story and the frontend
# calls it, so the exported contract deliberately includes it — a client deploy
# simply 404s those paths (main.py). Forced here, before the app import reads it.
os.environ["ENABLE_DEMO_ROUTES"] = "1"

from brokerops_api.main import app  # noqa: E402  (env pin must precede the import)

OUTPUT = Path(__file__).resolve().parent.parent / "frontend" / "openapi.json"


def main() -> int:
    spec = app.openapi()
    OUTPUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"wrote {OUTPUT.relative_to(Path.cwd()) if OUTPUT.is_relative_to(Path.cwd()) else OUTPUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
