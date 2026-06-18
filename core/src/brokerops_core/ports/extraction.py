from typing import Protocol

from brokerops_core.services.feedback_extraction import ExtractedFeedback


class ExtractionPort(Protocol):
    """Boundary to feedback-transcript extraction.

    The ExtractedFeedback schema is the contract; how it's produced is the
    adapter's concern. The deterministic default keeps demo mode
    zero-credential; an LLM-backed adapter raises recall when a provider key
    is configured (ADR-0006). core/ depends only on this Protocol.
    """

    async def extract(self, transcript: str) -> ExtractedFeedback: ...
