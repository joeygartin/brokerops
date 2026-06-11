"""Structured extraction from showing-feedback call transcripts.

The output contract is the Pydantic model — workflows and CRM sync depend on
its shape, not on how it was produced. This implementation is deterministic
(keyword/pattern based) so demo mode runs with zero credentials; an LLM-backed
extractor replaces the body of `extract_feedback` without touching its
signature or schema.
"""

import re

from pydantic import BaseModel, Field

from brokerops_core.models.feedback import Sentiment

POSITIVE_CUES = (
    "loved",
    "love",
    "great",
    "beautiful",
    "perfect",
    "amazing",
    "wonderful",
    "impressed",
    "spacious",
)
NEGATIVE_CUES = (
    "overpriced",
    "too expensive",
    "dated",
    "needs updating",
    "needs work",
    "small",
    "cramped",
    "disappointed",
    "keep looking",
    "not for us",
)
HOT_PHRASES = (
    "write an offer",
    "make an offer",
    "put in an offer",
    "submit an offer",
    "ready to move forward",
)
FEATURES = (
    "kitchen",
    "backyard",
    "yard",
    "garage",
    "bathroom",
    "bath",
    "bedroom",
    "location",
    "neighborhood",
    "light",
    "layout",
    "pool",
    "view",
)

# Spoken real-estate price shorthand: "four fifty" → 450 → $450,000.
_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}  # fmt: skip
_TENS = {
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip

_NUMERIC_PRICE = re.compile(r"\$?(\d{2,3}(?:,\d{3})*|\d{2,3})\s*(k|thousand)?", re.IGNORECASE)
_SPOKEN_PRICE = re.compile(
    rf"\b({'|'.join(_UNITS)})\s+({'|'.join(_TENS)})(?:\s+({'|'.join(_UNITS)}))?\b", re.IGNORECASE
)
_RANGE = re.compile(r"between\s+(.{1,40}?)\s+and\s+(.{1,40}?)(?:[.,;]|$)", re.IGNORECASE)


class ExtractedFeedback(BaseModel):
    sentiment: Sentiment
    hot_signal: bool = False
    highlights: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    price_opinion: str | None = None
    budget_min: int | None = Field(default=None, description="Whole dollars")
    budget_max: int | None = Field(default=None, description="Whole dollars")
    summary: str = ""


def _parse_price_phrase(phrase: str) -> int | None:
    """Parse one spoken or written price into whole dollars ('four fifty' → 450_000)."""
    spoken = _SPOKEN_PRICE.search(phrase)
    if spoken:
        hundreds = _UNITS[spoken.group(1).lower()] * 100
        tens = _TENS[spoken.group(2).lower()]
        units = _UNITS[spoken.group(3).lower()] if spoken.group(3) else 0
        return (hundreds + tens + units) * 1000
    numeric = _NUMERIC_PRICE.search(phrase)
    if numeric:
        value = int(numeric.group(1).replace(",", ""))
        if numeric.group(2) or value < 1000:  # "450k" or bare "450" both mean thousands
            value *= 1000
        return value
    return None


def parse_budget_range(text: str) -> tuple[int, int] | None:
    match = _RANGE.search(text)
    if not match:
        return None
    low = _parse_price_phrase(match.group(1))
    high = _parse_price_phrase(match.group(2))
    if low is None or high is None:
        return None
    return (min(low, high), max(low, high))


def _sentence_features(sentence: str) -> list[str]:
    lowered = sentence.lower()
    return [feature for feature in FEATURES if feature in lowered]


def extract_feedback(transcript: str) -> ExtractedFeedback:
    lowered = transcript.lower()
    sentences = re.split(r"[.!?]+", transcript)

    highlights: list[str] = []
    concerns: list[str] = []
    score = 0
    for sentence in sentences:
        sentence_lower = sentence.lower()
        positive = any(cue in sentence_lower for cue in POSITIVE_CUES)
        negative = any(cue in sentence_lower for cue in NEGATIVE_CUES)
        score += int(positive) - int(negative)
        if positive and not negative:
            highlights.extend(f for f in _sentence_features(sentence) if f not in highlights)
        if negative:
            concerns.extend(f for f in _sentence_features(sentence) if f not in concerns)

    sentiment = (
        Sentiment.POSITIVE if score > 0 else Sentiment.NEGATIVE if score < 0 else Sentiment.NEUTRAL
    )
    hot_signal = any(phrase in lowered for phrase in HOT_PHRASES)

    if "overpriced" in lowered or "too expensive" in lowered:
        price_opinion: str | None = "overpriced"
    elif "good value" in lowered or "great price" in lowered or "priced right" in lowered:
        price_opinion = "fair"
    else:
        price_opinion = None

    budget = parse_budget_range(transcript)

    parts = [f"Sentiment: {sentiment.value}."]
    if hot_signal:
        parts.append("HOT: buyer signaled offer intent.")
    if highlights:
        parts.append(f"Liked: {', '.join(highlights)}.")
    if concerns:
        parts.append(f"Concerns: {', '.join(concerns)}.")
    if price_opinion:
        parts.append(f"Price opinion: {price_opinion}.")
    if budget:
        parts.append(f"Budget: ${budget[0]:,}–${budget[1]:,}.")

    return ExtractedFeedback(
        sentiment=sentiment,
        hot_signal=hot_signal,
        highlights=highlights,
        concerns=concerns,
        price_opinion=price_opinion,
        budget_min=budget[0] if budget else None,
        budget_max=budget[1] if budget else None,
        summary=" ".join(parts),
    )
