"""The closed EMAIL_PROVIDER selector (ADR-0015, the ADR-0014 posture).

Selection is explicit deploy config, never key-presence inference. Under test:
unset/blank defaults to the zero-credential stub; `ses` selects the SES adapter
and fails loud on missing config (BOP-016); `sendgrid` is declared but fails
loud until BOP-017 wires its adapter (never a silent downgrade to the stub);
an unknown value fails closed, mirroring ORCHESTRATOR/EXTRACTION_BACKEND.
"""

import pytest

from brokerops_api.deps import build_email_port
from brokerops_email_ses.adapter import SESEmailAdapter
from brokerops_email_stub.adapter import StubEmailAdapter

SES_ENV = {
    "SES_ACCESS_KEY_ID": "fake-ses-access-key-id",
    "SES_SECRET_ACCESS_KEY": "fake-ses-secret-key",
    "SES_FROM_ADDRESS": "updates@demo.example.com",
}


def test_unset_selector_defaults_to_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.delenv("EMAIL_BASE_URL", raising=False)
    assert isinstance(build_email_port(), StubEmailAdapter)


def test_explicit_stub_selects_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "stub")
    assert isinstance(build_email_port(), StubEmailAdapter)


def test_stub_honors_an_external_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "stub")
    monkeypatch.setenv("EMAIL_BASE_URL", "http://localhost:8025")
    assert isinstance(build_email_port(), StubEmailAdapter)


def test_ses_with_config_selects_the_ses_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    for name, value in SES_ENV.items():
        monkeypatch.setenv(name, value)
    assert isinstance(build_email_port(), SESEmailAdapter)


@pytest.mark.parametrize("missing", sorted(SES_ENV))
def test_ses_with_missing_config_fails_loud(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    for name, value in SES_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        build_email_port()


def test_ses_with_the_terraform_placeholder_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # "unset" is the placeholder Terraform seeds a never-pushed secret with —
    # the same misconfiguration as a missing variable, never a downgrade.
    monkeypatch.setenv("EMAIL_PROVIDER", "ses")
    for name, value in SES_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SES_SECRET_ACCESS_KEY", "unset")
    with pytest.raises(RuntimeError, match="SES_SECRET_ACCESS_KEY"):
        build_email_port()


def test_declared_but_unwired_sendgrid_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    with pytest.raises(RuntimeError, match="not yet wired"):
        build_email_port()


def test_unknown_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "pigeon")
    with pytest.raises(RuntimeError, match="unknown EMAIL_PROVIDER"):
        build_email_port()
