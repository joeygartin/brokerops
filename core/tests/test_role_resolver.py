"""RoleResolver mapping + Role hierarchy — no I/O, no framework."""

from brokerops_core.ports.identity import Role
from brokerops_core.services.identity import RoleResolver


def test_role_hierarchy() -> None:
    assert Role.ADMIN.allows(Role.ADMIN)
    assert Role.ADMIN.allows(Role.OPERATOR)
    assert Role.ADMIN.allows(Role.VIEWER)
    assert Role.OPERATOR.allows(Role.VIEWER)
    assert not Role.OPERATOR.allows(Role.ADMIN)
    assert not Role.VIEWER.allows(Role.OPERATOR)
    assert not Role.VIEWER.allows(Role.ADMIN)


def test_unrestricted_grants_admin_to_everyone() -> None:
    # No rule configured → behaves as the pre-RBAC flat operator list.
    rr = RoleResolver()
    assert rr.unrestricted is True
    assert rr.role_for("anyone@anywhere.com") is Role.ADMIN


def test_admin_email_match_is_case_insensitive() -> None:
    rr = RoleResolver(admin_emails=frozenset({"Boss@Acme.com"}))
    assert rr.role_for("boss@acme.com") is Role.ADMIN
    # Restricted now (an admin rule is set), so an unmatched email is an operator.
    assert rr.role_for("staff@acme.com") is Role.OPERATOR


def test_viewer_rule_assigns_viewer_else_operator() -> None:
    rr = RoleResolver(
        admin_emails=frozenset({"boss@acme.com"}),
        viewer_emails=frozenset({"auditor@acme.com"}),
    )
    assert rr.role_for("boss@acme.com") is Role.ADMIN
    assert rr.role_for("auditor@acme.com") is Role.VIEWER
    assert rr.role_for("agent@acme.com") is Role.OPERATOR


def test_domain_rules_match_via_email_or_hosted_claim() -> None:
    rr = RoleResolver(admin_domain="acme.com", viewer_domain="contractors.com")
    assert rr.role_for("x@acme.com") is Role.ADMIN
    assert rr.role_for("x@other.com", hosted_domain="acme.com") is Role.ADMIN
    assert rr.role_for("x@contractors.com") is Role.VIEWER
    assert rr.role_for("x@somewhere.com") is Role.OPERATOR


def test_admin_takes_precedence_over_viewer() -> None:
    rr = RoleResolver(
        admin_emails=frozenset({"both@acme.com"}),
        viewer_emails=frozenset({"both@acme.com"}),
    )
    assert rr.role_for("both@acme.com") is Role.ADMIN
