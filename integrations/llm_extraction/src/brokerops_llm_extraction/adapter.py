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

from brokerops_core.services.feedback_extraction import ExtractedFeedback

DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """\
You extract structured data from a real-estate showing-feedback phone call \
transcript. The transcript is auto-transcribed speech: expect disfluencies, \
filler, and numbers written as words or digit groups. Return only the schema.

Field rules:
- sentiment: the buyer's overall feeling about THIS home (positive / neutral / \
negative). A home the buyer liked but that doesn't fit their needs (too small, \
wrong location) is still positive or neutral about the home itself — record the \
fit problems under concerns, not as negative sentiment.
- hot_signal: true only when the buyer expresses genuine intent to make an offer \
or move forward on THIS home. Be negation-aware: "I'm not going to write an \
offer" or "I don't need a second showing" is NOT a hot signal.
- highlights: features the buyer liked (e.g. kitchen, layout, master bedroom).
- concerns: features or attributes the buyer disliked or found lacking (e.g. \
backyard too small, dated bathroom, not enough space).
- price_opinion: overpriced, fair, or underpriced from the buyer's view of price \
versus value; null if price was not discussed.
- budget_min / budget_max: the buyer's budget range in whole dollars if stated. \
Resolve spoken/transcribed forms: "between 5 50 and 6" means 550000 to 600000; \
"four fifty to five and a quarter" means 450000 to 525000. null if not stated.
- desired_features: what the buyer wants in a home they would pursue, for sending \
better future matches (e.g. "one more bedroom", "at least half an acre", \
"bigger backyard"). Empty if none mentioned.
- summary: one concise sentence a listing agent can read at a glance.
"""


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
