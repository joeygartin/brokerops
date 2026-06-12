"""Business rules for what happens around a listing's marketing lifecycle."""

from brokerops_core.models.listing import Listing, ListingStatus
from brokerops_core.models.marketing import MarketingDraft


def is_marketable(listing: Listing) -> bool:
    # Address is load-bearing: the flyer task and the draft body both need it.
    return listing.status is ListingStatus.ACTIVE and bool(listing.address)


def plan_marketing_tasks(listing: Listing, draft: MarketingDraft) -> list[str]:
    """The CRM task set an approved marketing draft fans out into.

    Returned as descriptions; the workflow routes them to the CRM through
    CRMPort once the FollowUpBoss integration lands.
    """
    tasks = [
        f"Publish approved marketing copy for {listing.mls_id} to: {', '.join(draft.channels)}",
        f"Add listing flyer for {listing.address} to the office print queue",
        f"Schedule first open house for {listing.mls_id}",
    ]
    if listing.list_price >= 750000:
        tasks.append(f"Notify luxury buyer list about {listing.mls_id}")
    return tasks
