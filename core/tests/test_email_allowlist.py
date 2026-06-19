from brokerops_core.services.identity import EmailAllowlist


def test_unrestricted_permits_anyone() -> None:
    al = EmailAllowlist()
    assert al.unrestricted is True
    assert al.permits("anyone@anywhere.com") is True


def test_email_list_is_case_insensitive() -> None:
    al = EmailAllowlist(allowed_emails=frozenset({"Person@Acme.com"}))
    assert al.permits("person@acme.com") is True
    assert al.permits("other@acme.com") is False


def test_domain_matches_via_email_or_hosted_claim() -> None:
    al = EmailAllowlist(allowed_domain="acme.com")
    assert al.permits("x@acme.com") is True
    assert al.permits("x@other.com", hosted_domain="acme.com") is True
    assert al.permits("x@other.com", hosted_domain="other.com") is False


def test_email_allow_passes_even_off_domain() -> None:
    al = EmailAllowlist(allowed_domain="acme.com", allowed_emails=frozenset({"guest@gmail.com"}))
    assert al.permits("guest@gmail.com") is True
    assert al.permits("nope@gmail.com") is False
