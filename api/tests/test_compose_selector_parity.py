"""Compose ↔ selector parity: every closed selector reaches the container.

The fail-loud selectors (ORCHESTRATOR, EXTRACTION_BACKEND, EMAIL_PROVIDER —
ADR-0014/0015) only protect a deploy if the value actually reaches the process.
docker compose does NOT forward the host environment or `.env` into a service
automatically — each variable must be interpolated in the api service's
`environment` block. A selector missing there means a `.env` with
EMAIL_PROVIDER=ses (or a typo) silently runs the default inside the container:
the exact silent downgrade the selectors exist to prevent, on the primary local
deploy surface. This test pins the passthrough for every selector env var the
api reads, so adding a selector without plumbing it fails CI.
"""

from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Every closed-enum selector (and its companion config) build_* wiring reads.
SELECTOR_VARS = (
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
    "SMS_PROVIDER",
    "SMS_BASE_URL",
    # Not a selector, but the SMS delivery webhook's fail-closed signing key
    # (BOP-018): if it doesn't reach the container, every callback 500s.
    "TWILIO_AUTH_TOKEN",
)


@pytest.mark.parametrize("var", SELECTOR_VARS)
def test_compose_passes_the_selector_into_the_api_container(var: str) -> None:
    text = COMPOSE.read_text()
    assert f"{var}: ${{{var}:-" in text, (
        f"docker-compose.yml does not pass {var} into the api service; a .env value "
        f"would be silently ignored in the container (add `{var}: ${{{var}:-...}}` "
        f"to the api environment block)"
    )
