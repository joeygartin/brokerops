"""Message templates are versioned source (ADR-0005/ADR-0015): deterministic
rendering, loud failures on unknown refs and missing parameters."""

import pytest

from brokerops_core.models.message_templates import (
    SHOWING_FOLLOWUP_V1,
    TEMPLATES,
    TemplateParamError,
    UnknownTemplateError,
    get_template,
)

PARAMS = {
    "recipient_name": "Sam",
    "listing_address": "412 Alder Court",
    "sender_name": "The Rivermouth Team",
}


def test_registry_is_keyed_by_versioned_ref() -> None:
    assert SHOWING_FOLLOWUP_V1.ref == "showing_followup:v1"
    assert TEMPLATES["showing_followup:v1"] is SHOWING_FOLLOWUP_V1
    assert get_template("showing_followup:v1") is SHOWING_FOLLOWUP_V1


def test_render_is_deterministic_and_substitutes_every_param() -> None:
    subject, body = SHOWING_FOLLOWUP_V1.render(PARAMS)
    assert subject == "Following up on your tour of 412 Alder Court"
    assert "Hi Sam," in body
    assert "412 Alder Court" in body
    assert body.endswith("The Rivermouth Team")
    assert "$" not in subject and "$" not in body  # nothing left unrendered
    assert SHOWING_FOLLOWUP_V1.render(PARAMS) == (subject, body)  # pure function


def test_missing_param_fails_loud() -> None:
    incomplete = {k: v for k, v in PARAMS.items() if k != "listing_address"}
    with pytest.raises(TemplateParamError, match="listing_address"):
        SHOWING_FOLLOWUP_V1.render(incomplete)


def test_unknown_ref_fails_loud_and_names_the_known_refs() -> None:
    with pytest.raises(UnknownTemplateError, match="showing_followup:v1"):
        get_template("showing_followup:v9")
