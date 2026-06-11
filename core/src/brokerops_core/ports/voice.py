from typing import Any, Protocol

from brokerops_core.models.call import CallRecord


class VoicePort(Protocol):
    """Boundary to the voice platform (Vapi in V1)."""

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str: ...

    async def get_call(self, call_id: str) -> CallRecord | None: ...
