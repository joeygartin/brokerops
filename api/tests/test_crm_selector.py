"""CRM_VENDOR selector behavior (ADR-0016, the ORCHESTRATOR/ADR-0014 posture):
explicit, closed, fail-loud. Unset keeps the FollowUpBoss default so the
zero-credential demo is unchanged; misconfiguration is a startup error, never
a silent fallback to a different CRM.
"""

import pytest

from brokerops_api.deps import build_crm_adapter, crm_vendor
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_sierra_crm.adapter import SierraCRMAdapter


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "CRM_VENDOR",
        "FUB_API_KEY",
        "FUB_BASE_URL",
        "SIERRA_API_KEY",
        "SIERRA_BASE_URL",
        "SIERRA_TASK_ASSIGNEE_ID",
        "SIERRA_TASK_ANCHOR_LEAD_ID",
    ):
        monkeypatch.delenv(var, raising=False)


def test_unset_defaults_to_followupboss() -> None:
    assert crm_vendor() == "followupboss"
    assert isinstance(build_crm_adapter(), FUBCRMAdapter)


def test_explicit_followupboss_selects_fub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_VENDOR", "followupboss")
    assert isinstance(build_crm_adapter(), FUBCRMAdapter)


def test_unknown_vendor_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_VENDOR", "salesforce")
    with pytest.raises(RuntimeError, match="unknown CRM_VENDOR"):
        build_crm_adapter()


def test_sierra_without_credentials_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_VENDOR", "sierra")
    with pytest.raises(RuntimeError, match="SIERRA_API_KEY"):
        build_crm_adapter()


def test_sierra_with_placeholder_key_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # "unset" is the Terraform placeholder for a secret that was never pushed.
    monkeypatch.setenv("CRM_VENDOR", "sierra")
    monkeypatch.setenv("SIERRA_API_KEY", "unset")
    with pytest.raises(RuntimeError, match="SIERRA_API_KEY"):
        build_crm_adapter()


def test_sierra_without_task_config_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # The key alone is not a working Sierra deploy: task writes need an
    # assignee and an anchor lead, so their absence is the same startup error.
    monkeypatch.setenv("CRM_VENDOR", "sierra")
    monkeypatch.setenv("SIERRA_API_KEY", "real-key")
    with pytest.raises(RuntimeError, match="SIERRA_TASK_ASSIGNEE_ID"):
        build_crm_adapter()


def test_sierra_fully_configured_builds_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_VENDOR", "sierra")
    monkeypatch.setenv("SIERRA_API_KEY", "real-key")
    monkeypatch.setenv("SIERRA_TASK_ASSIGNEE_ID", "4321")
    monkeypatch.setenv("SIERRA_TASK_ANCHOR_LEAD_ID", "501")
    assert isinstance(build_crm_adapter(), SierraCRMAdapter)


def test_sierra_internal_stub_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Demo posture parity with FUB: base_url "internal" mounts the stub
    # in-process, so a sierra demo deploy stays zero-credential.
    monkeypatch.setenv("CRM_VENDOR", "sierra")
    monkeypatch.setenv("SIERRA_BASE_URL", "internal")
    assert isinstance(build_crm_adapter(), SierraCRMAdapter)
