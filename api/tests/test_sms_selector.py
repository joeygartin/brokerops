"""The closed SMS_PROVIDER selector (BOP-018, the ADR-0014/0015 posture).

Selection is explicit deploy config, never key-presence inference. Under test:
unset/blank defaults to the zero-credential Twilio-shaped stub; `twilio` with
missing account/auth/sender config fails loud (never a silent downgrade to the
stub); an unknown value fails closed, mirroring EMAIL_PROVIDER/ORCHESTRATOR.
"""

import pytest

from brokerops_api.deps import build_sms_port
from brokerops_twilio_sms.adapter import TwilioSMSAdapter

TWILIO_VARS = (
    "SMS_PROVIDER",
    "SMS_BASE_URL",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TWILIO_MESSAGING_SERVICE_SID",
    "TWILIO_STATUS_CALLBACK_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in TWILIO_VARS:
        monkeypatch.delenv(var, raising=False)


def test_unset_selector_defaults_to_the_stub() -> None:
    assert isinstance(build_sms_port(), TwilioSMSAdapter)


def test_explicit_stub_selects_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "stub")
    assert isinstance(build_sms_port(), TwilioSMSAdapter)


def test_twilio_without_credentials_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    with pytest.raises(RuntimeError, match="requires TWILIO_ACCOUNT_SID"):
        build_sms_port()


def test_twilio_with_placeholder_secret_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # "unset" is the Terraform placeholder for a secret that was never pushed.
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "unset")
    with pytest.raises(RuntimeError, match="requires TWILIO_AUTH_TOKEN"):
        build_sms_port()


def test_twilio_without_a_sender_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "real-token")
    with pytest.raises(RuntimeError, match="TWILIO_FROM_NUMBER or"):
        build_sms_port()


@pytest.mark.parametrize("sender_var", ["TWILIO_FROM_NUMBER", "TWILIO_MESSAGING_SERVICE_SID"])
def test_twilio_fully_configured_builds_the_adapter(
    monkeypatch: pytest.MonkeyPatch, sender_var: str
) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "real-token")
    monkeypatch.setenv(sender_var, "+15005550006" if "NUMBER" in sender_var else "MGreal0000")
    assert isinstance(build_sms_port(), TwilioSMSAdapter)


def test_unknown_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "carrier-pigeon")
    with pytest.raises(RuntimeError, match="unknown SMS_PROVIDER"):
        build_sms_port()


def _configure_real_twilio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMS_PROVIDER", "twilio")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACreal000000000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "real-token")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")


def test_missing_callback_pin_logs_a_startup_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # BOP-037: an unset pin silently disables delivery tracking (rows stay SENT
    # forever) and 401s console-level callbacks behind a proxy — the deploy
    # still starts, but the operator gets one loud, greppable line about it.
    _configure_real_twilio(monkeypatch)
    with caplog.at_level("WARNING", logger="brokerops_api.deps"):
        assert isinstance(build_sms_port(), TwilioSMSAdapter)
    warnings = [r for r in caplog.records if "TWILIO_STATUS_CALLBACK_URL" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelname == "WARNING"
    assert "delivery-status callbacks are not requested" in warnings[0].getMessage()


def test_configured_callback_pin_logs_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _configure_real_twilio(monkeypatch)
    monkeypatch.setenv(
        "TWILIO_STATUS_CALLBACK_URL", "https://api.client.example/webhooks/twilio-sms"
    )
    with caplog.at_level("WARNING", logger="brokerops_api.deps"):
        assert isinstance(build_sms_port(), TwilioSMSAdapter)
    assert not [r for r in caplog.records if "TWILIO_STATUS_CALLBACK_URL" in r.getMessage()]
