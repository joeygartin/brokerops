from typing import Protocol

from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery


class MLSPort(Protocol):
    """Boundary to an MLS data source.

    Services depend on this protocol, never on a concrete client — the same
    service code runs against the mock RESO server, a live RESO feed, or an
    in-memory fake in tests.
    """

    async def search_listings(self, query: ListingQuery) -> list[Listing]: ...

    async def get_listing(self, listing_key: str) -> Listing | None: ...

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]: ...
