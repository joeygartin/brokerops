"""API test setup shared across the suite.

The demo seed/reset routes are mounted only when ENABLE_DEMO_ROUTES is set (BOP-007),
and main.py decides that at import time. Enable it here — before any test module imports
`brokerops_api.main` — so the suite can exercise `/demo/seed` through the real wiring. The
absent-when-disabled behavior is covered by a unit test on `demo_routes_enabled()`.
"""

import os

os.environ.setdefault("ENABLE_DEMO_ROUTES", "true")
