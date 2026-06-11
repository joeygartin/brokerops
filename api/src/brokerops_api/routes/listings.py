from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from brokerops_api.deps import get_listing_service
from brokerops_core.models.listing import Listing, ListingQuery, ListingStatus
from brokerops_core.services.listing_service import ListingService

router = APIRouter(prefix="/listings", tags=["listings"])

ListingServiceDep = Annotated[ListingService, Depends(get_listing_service)]


@router.get("")
async def search_listings(
    service: ListingServiceDep,
    status: ListingStatus | None = None,
    city: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    min_bedrooms: int | None = None,
    limit: int = 50,
) -> list[Listing]:
    query = ListingQuery(
        status=status,
        city=city,
        min_price=min_price,
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        limit=limit,
    )
    return await service.search(query)


@router.get("/{listing_key}")
async def get_listing(listing_key: str, service: ListingServiceDep) -> Listing:
    listing = await service.get_with_media(listing_key)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"listing {listing_key!r} not found")
    return listing
