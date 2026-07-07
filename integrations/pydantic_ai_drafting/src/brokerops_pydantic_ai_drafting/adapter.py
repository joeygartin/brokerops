"""DraftingPort adapter backed by a PydanticAI agent (BOP-020, ADR-0020).

The LLM-drafted outbound message. Same ``DraftingPort`` Protocol and
``DraftedMessage`` contract as the deterministic template drafter (BOP-019);
PydanticAI adds validation self-correction (schema errors are fed back to the
model and retried, not hand-parsed), a model-agnostic constructor (the provider
is an argument, not an import), and a ``UsageLimits`` bound so a retry loop can
never run unbounded — the ``pydantic_ai_extraction`` integration's shape, one
port over. Following ADR-0014 there is no raw-SDK twin: extraction already made
that comparison; drafting ships one LLM backend.

Trust boundary: the model contributes the *copy* (subject + body) only. Every
routing/identity field — channel, recipient, template_ref, and the entity refs —
is authoritative from the ``DraftContext`` and overlaid onto the model's output,
so a generated draft can never address itself to a recipient the model invented
or re-file itself against another entity. The draft is still persisted
PENDING_APPROVAL and reaches an external party only through the BOP-019 human
gate and the BOP-012 egress-filtered channel seam — the wiring hard-gate
(``deps.build_drafting_port``) refuses to enable this backend on a seam lacking
that filter.

Provider shapes never leave this module; the api selects this backend only when
DRAFTING_BACKEND=pydantic_ai is explicit — never by key inference.
"""

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from brokerops_core.models.drafting import DraftContext, DraftedMessage
from brokerops_core.services.drafting_prompt import DRAFTING_SYSTEM_PROMPT, render_draft_request

DEFAULT_MODEL = "claude-sonnet-5"

# Drafting is a single structured call that registers no tools: allow the initial
# request plus PydanticAI's validation-retry round trips, nothing else.
RUN_LIMITS = UsageLimits(request_limit=3)


class PydanticAIDraftingAdapter:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._agent = Agent(
            AnthropicModel(model, provider=AnthropicProvider(api_key=api_key)),
            output_type=DraftedMessage,
            instructions=DRAFTING_SYSTEM_PROMPT,
        )

    async def draft(self, context: DraftContext) -> DraftedMessage:
        result = await self._agent.run(render_draft_request(context), usage_limits=RUN_LIMITS)
        # The model writes the copy (subject + body); the routing/identity fields
        # are the context's, never the model's to invent — overlay them so a
        # generated draft cannot address itself to a recipient the model made up.
        return result.output.model_copy(
            update={
                "channel": context.channel,
                "recipient": context.recipient,
                "template_ref": context.template_ref,
                "contact_id": context.contact_id,
                "listing_key": context.listing_key,
                "transaction_id": context.transaction_id,
            }
        )
