"""DRAFTING_BACKEND selector (BOP-019/020): explicit, closed, fail-loud — the
ADR-0014 posture. Unset/deterministic → template rendering with zero
credentials; `pydantic_ai` is the LLM backend, hard-gated on the BOP-012 egress
filter being wired on the channel-port seam; unknown values refuse to start
rather than silently downgrading."""

import pytest

from brokerops_api.deps import build_drafting_port
from brokerops_core.services.drafting import DeterministicDrafter
from brokerops_core.services.egress import EGRESS_FILTERED_MARKER


class _Seam:
    """A stand-in for a wired channel-port seam."""


def _egress_filtered_channel() -> _Seam:
    seam = _Seam()
    setattr(seam, EGRESS_FILTERED_MARKER, True)
    return seam


def test_unset_selects_the_deterministic_drafter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DRAFTING_BACKEND", raising=False)
    assert isinstance(build_drafting_port(), DeterministicDrafter)


def test_explicit_deterministic_selects_the_deterministic_drafter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "deterministic")
    # The deterministic backend ignores the channel gate entirely.
    assert isinstance(build_drafting_port(object()), DeterministicDrafter)


def test_pydantic_ai_refuses_without_an_egress_filtered_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A key is present so the refusal is unambiguously the egress gate, not a
    # missing-credential error (the gate runs first regardless).
    monkeypatch.setenv("DRAFTING_BACKEND", "pydantic_ai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    with pytest.raises(RuntimeError, match="egress-filtered"):
        build_drafting_port()  # no channel supplied → fail closed
    with pytest.raises(RuntimeError, match="egress-filtered"):
        build_drafting_port(object())  # an unfiltered channel → fail closed


def test_pydantic_ai_builds_when_the_channel_is_egress_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "pydantic_ai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    from brokerops_pydantic_ai_drafting.adapter import PydanticAIDraftingAdapter

    port = build_drafting_port(_egress_filtered_channel())
    assert isinstance(port, PydanticAIDraftingAdapter)


def test_pydantic_ai_refuses_when_any_drafted_channel_is_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # main.py passes every channel a draft can egress through (email AND sms). If
    # one is filtered but another is not, the gate must still fail closed — a single
    # unguarded send path is enough to leak LLM copy (the SMS-seam blind spot).
    monkeypatch.setenv("DRAFTING_BACKEND", "pydantic_ai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    with pytest.raises(RuntimeError, match="egress-filtered"):
        build_drafting_port(_egress_filtered_channel(), object())


def test_pydantic_ai_requires_a_real_key_once_the_gate_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "pydantic_ai")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DRAFTING_BACKEND"):
        build_drafting_port(_egress_filtered_channel())


def test_unknown_backend_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAFTING_BACKEND", "chatbot9000")
    with pytest.raises(RuntimeError, match="unknown DRAFTING_BACKEND"):
        build_drafting_port()
