"""MCP server exposing the MLS tools: search_listings, get_listing, get_listing_media.

Runs standalone over stdio (`uv run mcp-server-mls-reso`); points at any RESO
Web API endpoint via RESO_BASE_URL (defaults to the local mock).
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from brokerops_core.models.listing import ListingQuery, ListingStatus
from brokerops_mls_reso.adapter import ResoMLSAdapter

mcp = FastMCP("mls-reso")


def _adapter() -> ResoMLSAdapter:
    return ResoMLSAdapter(
        os.environ.get("RESO_BASE_URL", "http://localhost:8001/odata"),
        auth_token=os.environ.get("RESO_AUTH_TOKEN") or None,
    )


async def search_listings(
    status: str | None = None,
    city: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    min_bedrooms: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search MLS listings. status is one of: active, pending, closed."""
    query = ListingQuery(
        status=ListingStatus(status) if status else None,
        city=city,
        min_price=min_price,
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        limit=limit,
    )
    listings = await _adapter().search_listings(query)
    return [listing.model_dump(mode="json") for listing in listings]


async def get_listing(listing_key: str) -> dict[str, Any] | None:
    """Fetch a single MLS listing by its listing key (e.g. RM1001)."""
    listing = await _adapter().get_listing(listing_key)
    return listing.model_dump(mode="json") if listing else None


async def get_listing_media(listing_key: str) -> list[dict[str, Any]]:
    """Fetch the media (photos) attached to an MLS listing, in display order."""
    media = await _adapter().get_listing_media(listing_key)
    return [item.model_dump(mode="json") for item in media]


mcp.tool()(search_listings)
mcp.tool()(get_listing)
mcp.tool()(get_listing_media)


def main() -> None:
    mcp.run()
