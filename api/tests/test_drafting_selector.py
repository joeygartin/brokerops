"""DRAFTING_BACKEND selector (BOP-019): explicit, closed, fail-loud — the
ADR-0014 posture. Unset/deterministic → template rendering with zero
credentials; llm is declared-but-unwired until BOP-020; unknown values refuse
to start rather than silently downgrading."""

import pytest

from brokerops_api.deps import build_drafting_port
from brokerops_core.services.drafting import DeterministicDrafter


def test_unset_selects_the_deterministic_drafter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DRAFTING_BACKEND", raising=False)
    assert isinstance(build_drafting_port(), DeterministicDrafter)


def test_explicit_deterministic_selects_the_deterministic_drafter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "deterministic")
    assert isinstance(build_drafting_port(), DeterministicDrafter)


def test_llm_fails_loud_until_its_adapter_lands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "llm")
    with pytest.raises(RuntimeError, match="BOP-020"):
        build_drafting_port()


def test_unknown_backend_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "chatbot9000")
    with pytest.raises(RuntimeError, match="unknown DRAFTING_BACKEND"):
        build_drafting_port()
