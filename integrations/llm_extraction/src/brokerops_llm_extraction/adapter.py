"""ExtractionPort adapter backed by Claude structured outputs.

The deterministic extractor (core) misses transcribed-numeral budgets,
price-opinion nuance, negated offer intent, and per-sentence feature
attribution on real transcribed speech. This adapter hands the transcript to
Claude and validates the reply against the same ExtractedFeedback schema, so
nothing downstream changes — only recall improves (ADR-0006).

Provider shapes never leave this module; the api selects this adapter over
the deterministic default only when an LLM key is configured.
"""

from anthropic import AsyncAnthropic

from brokerops_core.services.extraction_prompt import EXTRACTION_SYSTEM_PROMPT
from brokerops_core.services.feedback_extraction import ExtractedFeedback

DEFAULT_MODEL = "claude-sonnet-4-6"

# The prompt is versioned source shared by every LLM extraction backend
# (ADR-0005/ADR-0014); it lives in core next to the schema it phrases.
SYSTEM_PROMPT = EXTRACTION_SYSTEM_PROMPT


class ClaudeExtractionAdapter:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncAnthropic(api_key=api_key)

    async def extract(self, transcript: str) -> ExtractedFeedback:
        message = await self._client.messages.parse(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
            output_format=ExtractedFeedback,
        )
        parsed = message.parsed_output
        if parsed is None:
            raise RuntimeError(
                f"extraction returned no structured output (stop={message.stop_reason})"
            )
        return parsed

    async def aclose(self) -> None:
        await self._client.close()
