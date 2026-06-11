from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CallRecord(BaseModel):
    """A voice call, raw and extracted layers kept separate."""

    vapi_call_id: str
    contact_id: str = ""
    listing_key: str = ""
    transcript: str = ""
    outcome: str = ""
    extracted: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
