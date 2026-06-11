from brokerops_core.models.listing import Listing, ListingQuery
from brokerops_core.ports.mls import MLSPort


class ListingService:
    def __init__(self, mls: MLSPort) -> None:
        self._mls = mls

    async def search(self, query: ListingQuery) -> list[Listing]:
        return await self._mls.search_listings(query)

    async def get_with_media(self, listing_key: str) -> Listing | None:
        listing = await self._mls.get_listing(listing_key)
        if listing is None:
            return None
        media = await self._mls.get_listing_media(listing_key)
        return listing.model_copy(update={"media": sorted(media, key=lambda m: m.order)})
