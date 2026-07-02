"""The system prompt for LLM-backed feedback extraction (ADR-0005, ADR-0014).

The prompt is the natural-language phrasing of the ExtractedFeedback contract,
so it lives next to the schema as versioned source — one prompt, however many
LLM backends implement ExtractionPort. It is a plain string: core stays
framework-free; adapters in integrations/ decide how to send it.
"""

EXTRACTION_SYSTEM_PROMPT = """\
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
