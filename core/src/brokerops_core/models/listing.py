from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ListingStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    CLOSED = "closed"


class ListingMedia(BaseModel):
    media_key: str
    listing_key: str
    url: str
    order: int = 0
    description: str = ""


class Listing(BaseModel):
    mls_id: str
    status: ListingStatus
    # Live feeds omit these for non-residential inventory (land, commercial)
    # and the address for unaddressed parcels — absence is real data, not error.
    address: str = ""
    city: str
    state: str
    postal_code: str
    list_price: int = Field(description="List price in whole dollars")
    bedrooms: int | None = None
    bathrooms: int | None = None
    living_area_sqft: int | None = None
    year_built: int | None = None
    agent_id: str
    agent_name: str
    remarks: str = ""
    modified_at: datetime
    media: list[ListingMedia] = Field(default_factory=list)


class ListingQuery(BaseModel):
    status: ListingStatus | None = None
    city: str | None = None
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    min_bedrooms: int | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
