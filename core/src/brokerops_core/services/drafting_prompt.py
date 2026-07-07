"""The system prompt for LLM-backed outbound-message drafting (ADR-0005, ADR-0020).

Versioned source next to the DraftContext/DraftedMessage schema
(``models/drafting.py``): one prompt, however many LLM backends implement
``DraftingPort``. A plain string plus a context-rendering helper — core stays
framework-free; adapters in ``integrations/`` decide how to send it. The
deterministic drafter (``services/drafting.py``) does not use this: it renders the
versioned templates verbatim. An LLM backend grounds its generated copy in the
same ``DraftContext`` the templates consume.
"""

from brokerops_core.models.drafting import DraftContext

DRAFTING_SYSTEM_PROMPT = """\
You write one short outbound message from a residential real-estate brokerage to \
a client, on the brokerage's behalf. The message is a routine touchpoint (a \
showing follow-up, a milestone reminder), not a negotiation.

Voice: warm, professional, and concise — a couple of short paragraphs at most. \
Write in plain language a busy client will read on their phone.

Grounding rules (important):
- Use ONLY the facts in the provided details. Do not invent prices, dates, \
addresses, features, or commitments the details do not state.
- Never promise anything binding (no guarantees about price, offers, timelines, \
or outcomes). This is a friendly nudge, not a contract.
- Do not include links, tracking pixels, attachments, or any credential, key, or \
internal identifier.
- Sign off with the sender name from the details if one is given; otherwise close \
generically ("the team"). Do not fabricate a signer's name.

Output: fill only `subject` and `body`.
- `subject`: a short, specific email subject line. Leave it empty for an SMS \
channel (SMS has no subject).
- `body`: the message text. For an SMS channel keep it to one or two sentences.
The other fields (recipient, channel, template reference, and entity ids) are \
supplied by the system and will be overwritten — do not try to set them.
"""


def render_draft_request(context: DraftContext) -> str:
    """Render a ``DraftContext`` as the grounding the drafting model reads.

    The natural-language phrasing of the context contract, kept next to the
    prompt so both versions move together. Only the channel, the message purpose
    (``template_ref``), and the params are exposed — the recipient and entity ids
    are routing data the model must not see or set (the adapter overlays them
    onto the output), so they are deliberately omitted here.
    """
    lines = [
        f"Channel: {context.channel.value}",
        f"Message purpose: {context.template_ref}",
    ]
    if context.params:
        lines.append("Details to ground the message in (use only these facts):")
        lines.extend(f"- {key}: {value}" for key, value in context.params.items())
    return "\n".join(lines)
