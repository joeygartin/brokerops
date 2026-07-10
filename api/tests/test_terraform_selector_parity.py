"""Terraform ↔ selector parity: every closed selector reaches the Cloud Run container.

The compose-parity sibling pins the local surface; this pins the deploy surface.
Cloud Run env vars are not passed through from a `.env` or the host — each must be
an explicit `env` block in the api service (`infra/modules/brokerops/services.tf`),
sourced either from a Terraform variable (plain config) or a Secret Manager version
(secrets). A selector missing there means a client's tfvars value is silently
ignored and the container runs the default — the exact silent downgrade the
fail-loud selectors exist to prevent, on the primary deploy surface (BOP-016 caught
EMAIL_PROVIDER/SES_*/SMS_* missing here after they shipped in compose).

The blocks may be gated (a `dynamic "env"` that only emits when the feature is on)
so an unconfigured deploy is unaffected — this is a static check that the `name =
"VAR"` wiring exists, regardless of the guard around it. The search is scoped to the
`google_cloud_run_v2_service.api` resource block: a name appearing only in a comment,
the frontend service, or another resource must NOT satisfy parity (otherwise deleting
the real wiring could be masked). Adding a selector is a five-part edit — see
docs/adding-a-selector.md — and this test enforces the terraform half.
"""

import re
from pathlib import Path

import pytest
from selector_contract import SELECTOR_VARS

SERVICES_TF = (
    Path(__file__).resolve().parents[2] / "infra" / "modules" / "brokerops" / "services.tf"
)

# Resource declarations delimit the api service block. The api container's env
# blocks live between the api resource and the next resource (frontend) — scoping
# to this slice excludes comments/other resources so only real api wiring counts.
_API_RESOURCE = 'resource "google_cloud_run_v2_service" "api"'
_FRONTEND_RESOURCE = 'resource "google_cloud_run_v2_service" "frontend"'


def _strip_hcl_comments(block: str) -> str:
    """Drop `#`/`//` line comments so a commented-out `name = "VAR"` inside the api
    block can't masquerade as real wiring. Line comments only — services.tf uses no
    inline `#`, and stripping mid-string `#` would be wrong, so this is line-anchored
    (a line whose first non-blank char starts a comment)."""
    kept = [line for line in block.splitlines() if not line.lstrip().startswith(("#", "//"))]
    return "\n".join(kept)


def _api_service_block() -> str:
    text = SERVICES_TF.read_text()
    start = text.find(_API_RESOURCE)
    end = text.find(_FRONTEND_RESOURCE)
    assert start != -1, f"could not find the api service resource ({_API_RESOURCE}) in services.tf"
    assert end != -1 and end > start, (
        f"could not find the frontend service resource ({_FRONTEND_RESOURCE}) after the api "
        "resource in services.tf — the api-block scope can't be delimited"
    )
    return _strip_hcl_comments(text[start:end])


@pytest.mark.parametrize("var", SELECTOR_VARS)
def test_terraform_wires_the_selector_into_the_api_container(var: str) -> None:
    api_block = _api_service_block()
    # An env block names the var as `name = "VAR"` (spacing varies with hcl
    # alignment); the value may be a literal or a secret_key_ref on the lines below.
    pattern = rf'name\s*=\s*"{re.escape(var)}"'
    assert re.search(pattern, api_block), (
        f"the api service in infra/modules/brokerops/services.tf has no env block naming "
        f"{var}; a client's tfvars/secret value would be silently ignored and the container "
        f'would run the default (add an `env {{ name = "{var}" ... }}` block to the api '
        f"container — see docs/adding-a-selector.md)"
    )
