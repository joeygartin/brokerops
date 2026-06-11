from datetime import UTC, datetime

from fastapi.testclient import TestClient

from brokerops_api.deps import get_listing_service
from brokerops_api.main import app
from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus
from brokerops_core.services.listing_service import ListingService

LISTING = Listing(
    mls_id="RM1001",
    status=ListingStatus.ACTIVE,
    address="412 Alder Court, Rivermouth, CA 95890",
    city="Rivermouth",
    state="CA",
    postal_code="95890",
    list_price=489000,
    bedrooms=3,
    bathrooms=2,
    agent_id="AGT-001",
    agent_name="Dana Whitfield",
    modified_at=datetime(2026, 6, 8, tzinfo=UTC),
)
MEDIA = ListingMedia(
    media_key="RM1001-M1",
    listing_key="RM1001",
    url="https://example.test/1",
    order=1,
)


class FakeMLS:
    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        if query.status not in (None, ListingStatus.ACTIVE):
            return []
        return [LISTING]

    async def get_listing(self, listing_key: str) -> Listing | None:
        return LISTING if listing_key == "RM1001" else None

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        return [MEDIA] if listing_key == "RM1001" else []


app.dependency_overrides[get_listing_service] = lambda: ListingService(FakeMLS())
client = TestClient(app)


def test_search_listings_route() -> None:
    response = client.get("/listings", params={"status": "active"})
    assert response.status_code == 200
    body = response.json()
    assert [item["mls_id"] for item in body] == ["RM1001"]


def test_search_listings_rejects_bad_status() -> None:
    assert client.get("/listings", params={"status": "withdrawn"}).status_code == 422


def test_get_listing_includes_media() -> None:
    response = client.get("/listings/RM1001")
    assert response.status_code == 200
    assert [m["media_key"] for m in response.json()["media"]] == ["RM1001-M1"]


def test_get_listing_unknown_404() -> None:
    assert client.get("/listings/RM9999").status_code == 404
