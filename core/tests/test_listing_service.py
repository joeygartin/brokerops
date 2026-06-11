from datetime import UTC, datetime

from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery, ListingStatus
from brokerops_core.services.listing_service import ListingService


def _listing(mls_id: str, price: int) -> Listing:
    return Listing(
        mls_id=mls_id,
        status=ListingStatus.ACTIVE,
        address="412 Alder Ct",
        city="Rivermouth",
        state="CA",
        postal_code="95890",
        list_price=price,
        bedrooms=3,
        bathrooms=2,
        agent_id="AGT-001",
        agent_name="Dana Whitfield",
        modified_at=datetime(2026, 6, 1, tzinfo=UTC),
    )


class FakeMLS:
    """In-memory MLSPort fake — the pattern that keeps core testable."""

    def __init__(self, listings: list[Listing], media: list[ListingMedia]) -> None:
        self._listings = listings
        self._media = media

    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        return self._listings[: query.limit]

    async def get_listing(self, listing_key: str) -> Listing | None:
        return next((it for it in self._listings if it.mls_id == listing_key), None)

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        return [m for m in self._media if m.listing_key == listing_key]


async def test_search_delegates_to_port() -> None:
    service = ListingService(FakeMLS([_listing("RM1001", 489000)], []))
    results = await service.search(ListingQuery())
    assert [it.mls_id for it in results] == ["RM1001"]


async def test_get_with_media_attaches_sorted_media() -> None:
    media = [
        ListingMedia(media_key="M2", listing_key="RM1001", url="https://example.test/2", order=2),
        ListingMedia(media_key="M1", listing_key="RM1001", url="https://example.test/1", order=1),
    ]
    service = ListingService(FakeMLS([_listing("RM1001", 489000)], media))
    listing = await service.get_with_media("RM1001")
    assert listing is not None
    assert [m.media_key for m in listing.media] == ["M1", "M2"]


async def test_get_with_media_returns_none_for_unknown_key() -> None:
    service = ListingService(FakeMLS([], []))
    assert await service.get_with_media("RM9999") is None
