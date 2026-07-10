"""The canonical selector-var contract — single source of truth for both parity
tests (compose and terraform).

Home + import path (deliberate). This is a **repo-unique-named sibling module**,
imported as `from selector_contract import SELECTOR_VARS`. That is the workspace's
mandated pattern for a shared test helper, and the only collision-free one here:

- It is NOT in `conftest.py`: the workspace has several `tests/` dirs each with their
  own `conftest.py` and no `__init__.py`, so in a full-suite run `sys.modules["conftest"]`
  binds to whichever conftest loaded first (langgraph's), and `from conftest import
  SELECTOR_VARS` then ImportErrors — the documented cross-package `from conftest import`
  collision.
- It is NOT a package import (`from api.tests... import`): adding `__init__.py` to a
  test dir would flip the whole rootless layout to package imports and collide the
  per-package conftests.
- A repo-unique module name sidesteps both: pytest's rootless prepend-import inserts the
  test dir on sys.path, and the unique name never clashes in `sys.modules`. Determinism
  is pinned by pyproject (`testpaths`/rootdir), the same mechanism the entire suite
  already relies on.

A "selector" is a closed-enum env var that names which backend a deploy runs
(ORCHESTRATOR, EXTRACTION_BACKEND, EMAIL_PROVIDER, SMS_PROVIDER, DRAFTING_BACKEND,
ADR-0014/0015) plus the companion config each selected backend reads. Every one is
fail-loud: a named-but-misconfigured backend refuses to start rather than silently
downgrading to the stub. That guarantee only holds if the value reaches the container
process, so each var must be wired on every deploy surface —
`test_compose_selector_parity.py` pins compose, `test_terraform_selector_parity.py`
pins the Cloud Run env wiring in `infra/`. Adding a selector is a five-part edit
(docs/adding-a-selector.md); adding the var here makes both parity tests enforce the
compose + terraform halves.
"""

# Every closed-enum selector (and its companion config) the api's build_* wiring
# reads. Extend this tuple when a new selector (or a new companion the selected
# backend fails loud without) ships; both parity tests then require it wired.
SELECTOR_VARS: tuple[str, ...] = (
    "ORCHESTRATOR",
    "EXTRACTION_BACKEND",
    "EMAIL_PROVIDER",
    "EMAIL_BASE_URL",
    "SES_REGION",
    "SES_ACCESS_KEY_ID",
    "SES_SECRET_ACCESS_KEY",
    "SES_FROM_ADDRESS",
    "SES_BASE_URL",
    "SENDGRID_API_KEY",
    "SENDGRID_FROM_EMAIL",
    "SENDGRID_BASE_URL",
    "DRAFTING_BACKEND",
    "SMS_PROVIDER",
    "SMS_BASE_URL",
    # Not a selector, but the SMS delivery webhook's fail-closed signing key
    # (BOP-018): if it doesn't reach the container, every callback 500s.
    "TWILIO_AUTH_TOKEN",
)
