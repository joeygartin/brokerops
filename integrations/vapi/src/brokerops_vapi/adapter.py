"""VoicePort adapter speaking the Vapi REST API.

Bearer-key auth; the same adapter runs against the bundled stub (demo mode)
and api.vapi.ai — swapping is a base-URL + key change. Vapi payload shapes
never leave this module.
"""

from collections.abc import Mapping
from typing import Any

import httpx

from brokerops_core.models.call import CallRecord

VAPI_API_BASE = "https://api.vapi.ai"


def call_record_from_vapi(call: Mapping[str, Any]) -> CallRecord:
    metadata = call.get("metadata") or {}
    artifact = call.get("artifact") or {}
    return CallRecord(
        vapi_call_id=str(call["id"]),
        contact_id=str(metadata.get("contact_id", "")),
        listing_key=str(metadata.get("listing_key", "")),
        transcript=artifact.get("transcript", ""),
        outcome=call.get("endedReason") or call.get("status", ""),
    )


class VapiVoiceAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str = VAPI_API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )

    async def start_outbound_call(
        self, contact_id: str, assistant_id: str, context: dict[str, Any]
    ) -> str:
        body: dict[str, Any] = {
            "assistantId": assistant_id,
            "metadata": {**context, "contact_id": contact_id},
        }
        phone = context.get("phone")
        if phone:
            body["customer"] = {"number": phone}
        response = await self._client.post("/call", json=body)
        response.raise_for_status()
        return str(response.json()["id"])

    async def get_call(self, call_id: str) -> CallRecord | None:
        response = await self._client.get(f"/call/{call_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return call_record_from_vapi(response.json())
