"""Marketing draft generation.

Deterministic template drafting for now — demo mode runs with zero credentials.
An LLM-backed drafter can replace the body of `draft_marketing` without touching
its signature or the workflow that calls it.
"""

from brokerops_core.models.listing import Listing
from brokerops_core.models.marketing import MarketingDraft

DEFAULT_CHANNELS = ["mls_portals", "facebook", "email_list"]


def draft_marketing(listing: Listing) -> MarketingDraft:
    has_rooms = listing.bedrooms is not None and listing.bathrooms is not None
    sqft = f" | {listing.living_area_sqft:,} sqft" if listing.living_area_sqft else ""
    where = f" in {listing.city}" if listing.city else ""
    price = f" — ${listing.list_price:,}" if listing.list_price is not None else ""
    if has_rooms:
        headline = f"Just Listed: {listing.bedrooms} bed / {listing.bathrooms} bath{where}{price}"
        facts = f"{listing.bedrooms} bd / {listing.bathrooms} ba"
    else:
        # Land and commercial inventory has no room counts to lead with.
        headline = f"Just Listed{where}{price}"
        facts = listing.city
    body = (
        f"{listing.address}\n"
        f"{facts}{sqft}"
        f"{f' | built {listing.year_built}' if listing.year_built else ''}\n\n"
        f"{listing.remarks}\n\n"
        f"Contact {listing.agent_name} to schedule a showing. MLS# {listing.mls_id}."
    )
    return MarketingDraft(
        listing_key=listing.mls_id,
        headline=headline,
        body=body,
        channels=list(DEFAULT_CHANNELS),
    )
