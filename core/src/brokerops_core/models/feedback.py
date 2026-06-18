from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FeedbackSource(StrEnum):
    CALL = "call"
    FORM = "form"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class PriceOpinion(StrEnum):
    OVERPRICED = "overpriced"
    FAIR = "fair"
    UNDERPRICED = "underpriced"


class ShowingFeedback(BaseModel):
    id: str
    listing_key: str
    contact_id: str
    call_id: str | None = None
    source: FeedbackSource = FeedbackSource.CALL
    sentiment: Sentiment = Sentiment.NEUTRAL
    structured_answers: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
