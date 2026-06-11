from pydantic import BaseModel, Field


class MarketingDraft(BaseModel):
    listing_key: str
    headline: str
    body: str
    channels: list[str] = Field(default_factory=list)
