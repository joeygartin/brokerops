from datetime import UTC, datetime

from brokerops_core.models.listing import Listing, ListingStatus
from brokerops_core.services.followup_rules import is_marketable, plan_marketing_tasks
from brokerops_core.services.marketing import draft_marketing


def _listing(status: ListingStatus = ListingStatus.ACTIVE, price: int = 489000) -> Listing:
    return Listing(
        mls_id="RM1001",
        status=status,
        address="412 Alder Court, Rivermouth, CA 95890",
        city="Rivermouth",
        state="CA",
        postal_code="95890",
        list_price=price,
        bedrooms=3,
        bathrooms=2,
        living_area_sqft=1750,
        year_built=1999,
        agent_id="AGT-001",
        agent_name="Dana Whitfield",
        remarks="Updated single-story.",
        modified_at=datetime(2026, 6, 8, tzinfo=UTC),
    )


def test_draft_is_deterministic_and_grounded_in_listing() -> None:
    draft = draft_marketing(_listing())
    assert draft.listing_key == "RM1001"
    assert "$489,000" in draft.headline
    assert "Dana Whitfield" in draft.body
    assert draft.channels  # never empty


def test_only_active_listings_are_marketable() -> None:
    assert is_marketable(_listing(ListingStatus.ACTIVE))
    assert not is_marketable(_listing(ListingStatus.PENDING))
    assert not is_marketable(_listing(ListingStatus.CLOSED))


def test_luxury_listings_get_extra_task() -> None:
    base = plan_marketing_tasks(_listing(), draft_marketing(_listing()))
    luxury_listing = _listing(price=900000)
    luxury = plan_marketing_tasks(luxury_listing, draft_marketing(luxury_listing))
    assert len(luxury) == len(base) + 1
    assert any("luxury" in task.lower() for task in luxury)


def _land_listing() -> Listing:
    # Live MLS feeds carry land/commercial inventory with no room counts.
    return _listing().model_copy(
        update={"bedrooms": None, "bathrooms": None, "living_area_sqft": None}
    )


def test_land_listing_drafts_without_room_counts() -> None:
    draft = draft_marketing(_land_listing())
    assert draft.headline == "Just Listed in Rivermouth — $489,000"
    assert "None" not in draft.headline
    assert "None" not in draft.body


def test_addressless_listing_is_not_marketable() -> None:
    # The flyer task and draft body are address-grounded; an unaddressed
    # parcel can be active in the feed but is not marketable here.
    assert not is_marketable(_listing().model_copy(update={"address": ""}))


def test_unpriced_listing_is_not_marketable() -> None:
    assert not is_marketable(_listing().model_copy(update={"list_price": None}))
