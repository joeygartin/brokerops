"""MLSPort adapter speaking the RESO Web API (OData).

Runs identically against the bundled mock and a live RESO endpoint — swapping
MLS providers is a base-URL + auth change, with no signature changes upstream.
"""

from collections.abc import Mapping
from typing import Any

import httpx

from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus

RESO_STATUS_TO_CORE = {
    "Active": ListingStatus.ACTIVE,
    "Pending": ListingStatus.PENDING,
    "Closed": ListingStatus.CLOSED,
}
CORE_STATUS_TO_RESO = {v: k for k, v in RESO_STATUS_TO_CORE.items()}


def _odata_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def listing_from_reso(record: Mapping[str, Any]) -> Listing:
    raw_status = record.get("StandardStatus", "")
    status = RESO_STATUS_TO_CORE.get(raw_status)
    if status is None:
        raise ValueError(f"unmapped RESO StandardStatus {raw_status!r}")
    return Listing(
        mls_id=record["ListingKey"],
        status=status,
        address=record["UnparsedAddress"],
        city=record["City"],
        state=record["StateOrProvince"],
        postal_code=record["PostalCode"],
        list_price=record["ListPrice"],
        bedrooms=record["BedroomsTotal"],
        bathrooms=record["BathroomsTotalInteger"],
        living_area_sqft=record.get("LivingArea"),
        year_built=record.get("YearBuilt"),
        agent_id=record["ListAgentKey"],
        agent_name=record["ListAgentFullName"],
        remarks=record.get("PublicRemarks", ""),
        modified_at=record["ModificationTimestamp"],
    )


def media_from_reso(record: Mapping[str, Any]) -> ListingMedia:
    return ListingMedia(
        media_key=record["MediaKey"],
        listing_key=record["ResourceRecordKey"],
        url=record["MediaURL"],
        order=record.get("Order", 0),
        description=record.get("ShortDescription", ""),
    )


class ResoMLSAdapter:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
    ) -> None:
        # Live RESO endpoints (e.g. an MLS vendor's Web API) authorize with a
        # bearer token; the bundled mock needs none.
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        if client is None:
            client = httpx.AsyncClient(base_url=base_url, timeout=10.0, headers=headers)
        elif headers:
            client.headers.update(headers)
        self._client = client

    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        params: dict[str, str] = {"$top": str(query.limit), "$orderby": "ListPrice desc"}
        clauses: list[str] = []
        if query.status is not None:
            clauses.append(f"StandardStatus eq {_odata_string(CORE_STATUS_TO_RESO[query.status])}")
        if query.city is not None:
            clauses.append(f"City eq {_odata_string(query.city)}")
        # The contracted OData subset has gt/lt but not ge/le; prices and bed
        # counts are integers, so inclusive bounds shift by one.
        if query.min_price is not None:
            clauses.append(f"ListPrice gt {query.min_price - 1}")
        if query.max_price is not None:
            clauses.append(f"ListPrice lt {query.max_price + 1}")
        if query.min_bedrooms is not None:
            clauses.append(f"BedroomsTotal gt {query.min_bedrooms - 1}")
        if clauses:
            params["$filter"] = " and ".join(clauses)
        response = await self._client.get("/odata/Property", params=params)
        response.raise_for_status()
        return [listing_from_reso(record) for record in response.json()["value"]]

    async def get_listing(self, listing_key: str) -> Listing | None:
        safe_key = listing_key.replace("'", "''")
        response = await self._client.get(f"/odata/Property('{safe_key}')")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return listing_from_reso(response.json())

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        params = {
            "$filter": f"ResourceRecordKey eq {_odata_string(listing_key)}",
            "$orderby": "Order",
        }
        response = await self._client.get("/odata/Media", params=params)
        response.raise_for_status()
        return [media_from_reso(record) for record in response.json()["value"]]
