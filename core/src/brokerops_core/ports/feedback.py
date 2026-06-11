from typing import Protocol

from brokerops_core.models.call import CallRecord
from brokerops_core.models.feedback import ShowingFeedback


class FeedbackStore(Protocol):
    """Persistence boundary for call records and showing feedback."""

    async def save_call_record(self, record: CallRecord) -> None: ...

    async def get_call_record(self, vapi_call_id: str) -> CallRecord | None: ...

    async def upsert_feedback(self, feedback: ShowingFeedback) -> str: ...

    async def list_feedback(self, listing_key: str) -> list[ShowingFeedback]: ...
