import pytest
from pydantic import ValidationError

from brokerops_core.models.listing import Listing, ListingQuery, ListingStatus


def make_listing(**overrides: object) -> Listing:
    base: dict[str, object] = {
        "mls_id": "RM1001",
        "status": "active",
        "address": "412 Alder Ct",
        "city": "Rivermouth",
        "state": "CA",
        "postal_code": "95890",
        "list_price": 489000,
        "bedrooms": 3,
        "bathrooms": 2,
        "agent_id": "AGT-001",
        "agent_name": "Dana Whitfield",
        "modified_at": "2026-06-01T10:15:00Z",
    }
    base.update(overrides)
    return Listing.model_validate(base)


def test_listing_parses_status_enum() -> None:
    listing = make_listing()
    assert listing.status is ListingStatus.ACTIVE
    assert listing.media == []


def test_listing_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        make_listing(status="withdrawn")


def test_query_defaults_and_bounds() -> None:
    query = ListingQuery()
    assert query.limit == 50
    with pytest.raises(ValidationError):
        ListingQuery(limit=0)
    with pytest.raises(ValidationError):
        ListingQuery(min_price=-1)
