"""Outbound message templates as versioned source (ADR-0005, made literal again).

Templates live next to the `Message` model they render into, exactly like the
extraction prompt lives next to its schema: deterministic text is code — reviewed,
dated, and versioned by commits. A template is addressed by its versioned ref
("name:vN"); bumping the text is a new version, so a persisted `template_ref` always
points at the exact source that produced the message. LLM-drafted comms are
BOP-019/020 — nothing here calls a model.

Rendering is `string.Template.substitute`: a pure function of (template, params)
that fails loud on a missing parameter instead of sending a half-filled email.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from string import Template


class UnknownTemplateError(LookupError):
    """Raised when a template ref does not resolve to a registered template."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"unknown message template {ref!r}; expected one of {sorted(TEMPLATES)}")
        self.ref = ref


class TemplateParamError(ValueError):
    """Raised when rendering is missing a parameter the template requires."""

    def __init__(self, ref: str, param: str) -> None:
        super().__init__(f"template {ref!r} requires parameter {param!r}")
        self.ref = ref
        self.param = param


@dataclass(frozen=True)
class MessageTemplate:
    """A versioned, deterministic message template ($param placeholders)."""

    name: str
    version: int
    subject: str
    body: str

    @property
    def ref(self) -> str:
        return f"{self.name}:v{self.version}"

    def render(self, params: Mapping[str, str]) -> tuple[str, str]:
        """Render (subject, body). Raises TemplateParamError on a missing parameter."""
        try:
            return (
                Template(self.subject).substitute(params),
                Template(self.body).substitute(params),
            )
        except KeyError as exc:
            raise TemplateParamError(self.ref, str(exc.args[0])) from exc


SHOWING_FOLLOWUP_V1 = MessageTemplate(
    name="showing_followup",
    version=1,
    subject="Following up on your tour of $listing_address",
    body=(
        "Hi $recipient_name,\n"
        "\n"
        "Thank you for touring $listing_address. We would love to hear what you "
        "thought of the home — any feedback helps us find you the right fit.\n"
        "\n"
        "Reply to this email or give us a call anytime.\n"
        "\n"
        "$sender_name"
    ),
)

MILESTONE_REMINDER_V1 = MessageTemplate(
    name="milestone_reminder",
    version=1,
    subject="Reminder: $milestone_title is due $due_date",
    body=(
        "Hi $recipient_name,\n"
        "\n"
        "This is a reminder that $milestone_title for $listing_address is due "
        "on $due_date. Let us know if anything is blocking it.\n"
        "\n"
        "$sender_name"
    ),
)

TEMPLATES: dict[str, MessageTemplate] = {
    template.ref: template for template in (SHOWING_FOLLOWUP_V1, MILESTONE_REMINDER_V1)
}


def get_template(ref: str) -> MessageTemplate:
    """Resolve a versioned template ref ("name:vN"); raises UnknownTemplateError."""
    template = TEMPLATES.get(ref)
    if template is None:
        raise UnknownTemplateError(ref)
    return template
