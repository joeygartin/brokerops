"""Adapter tests — RESO records map into core models through MLSPort semantics."""

import httpx
import pytest

from brokerops_core.models.listing import ListingQuery, ListingStatus
from brokerops_mls_reso.adapter import ResoMLSAdapter, listing_from_reso
from brokerops_mls_reso.server import create_app


@pytest.fixture
def adapter() -> ResoMLSAdapter:
    transport = httpx.ASGITransport(app=create_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://mls.test")
    return ResoMLSAdapter(base_url="http://mls.test", client=client)


async def test_search_maps_reso_fields_to_core(adapter: ResoMLSAdapter) -> None:
    listings = await adapter.search_listings(ListingQuery(status=ListingStatus.ACTIVE, limit=3))
    assert len(listings) == 3
    top = listings[0]
    assert top.mls_id == "RM1009"  # adapter orders by price desc
    assert top.status is ListingStatus.ACTIVE
    assert top.list_price == 1150000
    assert top.agent_name == "Priya Raman"


async def test_search_price_bounds_are_inclusive(adapter: ResoMLSAdapter) -> None:
    listings = await adapter.search_listings(ListingQuery(min_price=489000, max_price=489000))
    assert [it.mls_id for it in listings] == ["RM1001"]


async def test_get_listing_found_and_missing(adapter: ResoMLSAdapter) -> None:
    listing = await adapter.get_listing("RM1003")
    assert listing is not None
    assert listing.bedrooms == 2
    assert await adapter.get_listing("RM9999") is None


async def test_get_listing_media_ordered(adapter: ResoMLSAdapter) -> None:
    media = await adapter.get_listing_media("RM1009")
    assert [m.order for m in media] == [1, 2, 3]
    assert all(m.listing_key == "RM1009" for m in media)


def test_unmapped_status_raises() -> None:
    record = {
        "ListingKey": "X1",
        "StandardStatus": "Withdrawn",
        "UnparsedAddress": "1 Test St",
        "City": "Rivermouth",
        "StateOrProvince": "CA",
        "PostalCode": "95890",
        "ListPrice": 1,
        "BedroomsTotal": 1,
        "BathroomsTotalInteger": 1,
        "ListAgentKey": "AGT-001",
        "ListAgentFullName": "Dana Whitfield",
        "ModificationTimestamp": "2026-06-01T00:00:00Z",
    }
    with pytest.raises(ValueError, match="unmapped RESO StandardStatus"):
        listing_from_reso(record)
