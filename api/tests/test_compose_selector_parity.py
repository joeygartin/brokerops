"""Compose ↔ selector parity: every closed selector reaches the container.

The fail-loud selectors (ORCHESTRATOR, EXTRACTION_BACKEND, EMAIL_PROVIDER —
ADR-0014/0015) only protect a deploy if the value actually reaches the process.
docker compose does NOT forward the host environment or `.env` into a service
automatically — each variable must be interpolated in the api service's
`environment` block. A selector missing there means a `.env` with
EMAIL_PROVIDER=ses (or a typo) silently runs the default inside the container:
the exact silent downgrade the selectors exist to prevent, on the primary local
deploy surface. This test pins the passthrough for every selector env var the
api reads, so adding a selector without plumbing it fails CI. The terraform
sibling (`test_terraform_selector_parity.py`) pins the Cloud Run deploy surface
from the same shared `SELECTOR_VARS`. Adding a selector is a five-part edit — see
docs/adding-a-selector.md.
"""

from pathlib import Path

import pytest
from selector_contract import SELECTOR_VARS

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.mark.parametrize("var", SELECTOR_VARS)
def test_compose_passes_the_selector_into_the_api_container(var: str) -> None:
    text = COMPOSE.read_text()
    assert f"{var}: ${{{var}:-" in text, (
        f"docker-compose.yml does not pass {var} into the api service; a .env value "
        f"would be silently ignored in the container (add `{var}: ${{{var}:-...}}` "
        f"to the api environment block)"
    )
