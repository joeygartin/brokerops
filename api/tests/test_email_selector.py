"""The closed EMAIL_PROVIDER selector (ADR-0015, the ADR-0014 posture).

Selection is explicit deploy config, never key-presence inference. Under test:
unset/blank defaults to the zero-credential stub; ses/sendgrid are declared but
fail loud until BOP-016/017 wire their adapters (never a silent downgrade to the
stub); an unknown value fails closed, mirroring ORCHESTRATOR/EXTRACTION_BACKEND.
"""

import pytest

from brokerops_api.deps import build_email_port
from brokerops_email_stub.adapter import StubEmailAdapter


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


@pytest.mark.parametrize("provider", ["ses", "sendgrid"])
def test_declared_but_unwired_providers_fail_loud(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", provider)
    with pytest.raises(RuntimeError, match="not yet wired"):
        build_email_port()


def test_unknown_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "pigeon")
    with pytest.raises(RuntimeError, match="unknown EMAIL_PROVIDER"):
        build_email_port()
