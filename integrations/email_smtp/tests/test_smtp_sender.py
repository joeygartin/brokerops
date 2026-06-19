"""SMTPEmailSender against a fake smtplib.SMTP — asserts the protocol dance, no network."""

from email.message import EmailMessage

import pytest

from brokerops_email_smtp.adapter import SMTPEmailSender


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int = 10) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: EmailMessage | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent = message


@pytest.fixture(autouse=True)
def _patch_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr("brokerops_email_smtp.adapter.smtplib.SMTP", FakeSMTP)


async def test_sends_with_starttls_and_login() -> None:
    sender = SMTPEmailSender(
        host="smtp.test",
        port=587,
        from_addr="no-reply@brokerops.app",
        username="user",
        password="pw",
    )
    await sender.send("op@acme.com", "Subj", "the link")
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.test", 587)
    assert smtp.started_tls is True
    assert smtp.login_args == ("user", "pw")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "op@acme.com"
    assert smtp.sent["From"] == "no-reply@brokerops.app"
    assert "the link" in smtp.sent.get_content()


async def test_skips_login_without_credentials() -> None:
    sender = SMTPEmailSender(host="smtp.test", port=25, from_addr="x@y.z", use_starttls=False)
    await sender.send("a@b.c", "S", "body")
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is False
    assert smtp.login_args is None
