"""ExtractionPort adapter backed by a PydanticAI agent (ADR-0014).

Sibling to the raw-SDK Claude adapter (ADR-0006): same ExtractionPort
Protocol, same ExtractedFeedback contract, same shared prompt. What PydanticAI
adds at this seam: validation self-correction (schema errors are fed back to
the model and retried instead of hand-rolled parse-retry), a model-agnostic
constructor (the provider is an argument, not an import), and a UsageLimits
bound on every run so a retry loop can never run unbounded.

Provider shapes never leave this module; the api selects this backend only
when EXTRACTION_BACKEND=pydantic_ai is explicit — never by key inference.
"""

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from brokerops_core.services.extraction_prompt import EXTRACTION_SYSTEM_PROMPT
from brokerops_core.services.feedback_extraction import ExtractedFeedback

DEFAULT_MODEL = "claude-sonnet-5"

# Extraction is a single structured call that registers no tools: allow the
# initial request plus PydanticAI's validation-retry round trips, nothing else.
RUN_LIMITS = UsageLimits(request_limit=3)


class PydanticAIExtractionAdapter:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._agent = Agent(
            AnthropicModel(model, provider=AnthropicProvider(api_key=api_key)),
            output_type=ExtractedFeedback,
            instructions=EXTRACTION_SYSTEM_PROMPT,
        )

    async def extract(self, transcript: str) -> ExtractedFeedback:
        result = await self._agent.run(transcript, usage_limits=RUN_LIMITS)
        return result.output
