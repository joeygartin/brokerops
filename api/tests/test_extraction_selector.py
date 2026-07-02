"""The closed EXTRACTION_BACKEND selector (ADR-0014).

Selection is explicit deploy config, never key-presence inference. The three
behaviors under test: unset/demo stays the zero-credential deterministic
default; an explicitly selected LLM backend with missing or placeholder config
fails loud at wiring time (never a silent downgrade); an unknown value fails
closed, mirroring the ORCHESTRATOR unknown-value guard.
"""

import pytest

from brokerops_api.deps import build_extraction_port
from brokerops_core.services.feedback_extraction import DeterministicExtractor
from brokerops_llm_extraction.adapter import ClaudeExtractionAdapter
from brokerops_pydantic_ai_extraction.adapter import PydanticAIExtractionAdapter


def test_unset_selector_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXTRACTION_BACKEND", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert isinstance(build_extraction_port(), DeterministicExtractor)


def test_unset_selector_ignores_a_present_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Key-presence inference is gone (ADR-0014): a key alone no longer selects
    # the LLM path — the backend must be named.
    monkeypatch.delenv("EXTRACTION_BACKEND", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-real-key")
    assert isinstance(build_extraction_port(), DeterministicExtractor)


def test_explicit_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTION_BACKEND", "deterministic")
    assert isinstance(build_extraction_port(), DeterministicExtractor)


def test_explicit_llm_selects_the_claude_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTION_BACKEND", "llm")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-real-key")
    assert isinstance(build_extraction_port(), ClaudeExtractionAdapter)


def test_explicit_pydantic_ai_selects_the_pydantic_ai_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTRACTION_BACKEND", "pydantic_ai")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-real-key")
    assert isinstance(build_extraction_port(), PydanticAIExtractionAdapter)


@pytest.mark.parametrize("backend", ["llm", "pydantic_ai"])
@pytest.mark.parametrize("key", ["", "unset"])
def test_explicit_llm_backend_without_a_key_fails_loud(
    monkeypatch: pytest.MonkeyPatch, backend: str, key: str
) -> None:
    # The round-2 blocker fix: explicit-but-misconfigured must raise, never
    # silently downgrade to deterministic. "unset" is the Terraform placeholder
    # for a secret that was never pushed.
    monkeypatch.setenv("EXTRACTION_BACKEND", backend)
    monkeypatch.setenv("LLM_API_KEY", key)
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        build_extraction_port()


def test_unknown_backend_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTION_BACKEND", "bogus")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-real-key")
    with pytest.raises(RuntimeError, match="unknown EXTRACTION_BACKEND"):
        build_extraction_port()
