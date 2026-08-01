from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field

from brokerops_core.models.sensitivity import RESTRICTED_CONTENT


class CallRecord(BaseModel):
    """A voice call, raw and extracted layers kept separate."""

    # Stamped by the scoped data layer (BOP-006).
    tenant_id: str = ""
    vapi_call_id: str
    contact_id: str = ""
    listing_key: str = ""
    # The verbatim client↔agent transcript is freeform operational content — the
    # same restricted class as a rendered message body (BOP-040): redacted at
    # egress for a viewer, who reads a call's outcome/linkage but not what was said.
    transcript: Annotated[str, RESTRICTED_CONTENT] = ""
    outcome: str = ""
    extracted: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
