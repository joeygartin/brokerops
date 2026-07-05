from typing import Protocol

from brokerops_core.models.drafting import DraftContext, DraftedMessage


class DraftingPort(Protocol):
    """Boundary to outbound-message drafting (BOP-019).

    The DraftContext/DraftedMessage schemas are the contract; how the text is
    produced is the backend's concern — exactly the ExtractionPort pattern
    (ADR-0006). The deterministic default renders the versioned core templates
    with zero credentials; an LLM-backed drafter (BOP-020) raises quality
    behind the same port. Either way the draft only ever reaches a person
    through the approve-outbound-message gate.
    """

    async def draft(self, context: DraftContext) -> DraftedMessage: ...
