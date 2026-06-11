import os
from functools import lru_cache

from brokerops_core.services.listing_service import ListingService
from brokerops_mls_reso.adapter import ResoMLSAdapter


@lru_cache(maxsize=1)
def get_listing_service() -> ListingService:
    base_url = os.environ.get("RESO_BASE_URL", "http://localhost:8001")
    return ListingService(ResoMLSAdapter(base_url=base_url))
