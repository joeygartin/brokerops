"""Internal-stub mode: base_url sentinel "internal" mounts each integration's
stub in-process — the configuration a zero-secret demo cloud deploy uses."""

import pytest

from brokerops_api.deps import build_crm_adapter, build_mls_adapter, build_voice_adapter
from brokerops_core.models.listing import ListingQuery


@pytest.fixture(autouse=True)
def internal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESO_BASE_URL", "internal")
    monkeypatch.setenv("FUB_BASE_URL", "internal")
    monkeypatch.setenv("VAPI_BASE_URL", "internal")
    monkeypatch.setenv("FUB_API_KEY", "internal-demo")
    monkeypatch.setenv("VAPI_API_KEY", "internal-demo")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


async def test_mls_internal_serves_seed() -> None:
    listings = await build_mls_adapter().search_listings(ListingQuery(limit=5))
    assert len(listings) == 5
    assert listings[0].list_price >= listings[-1].list_price


async def test_crm_internal_serves_stub_contacts() -> None:
    contacts = await build_crm_adapter().search_contacts("jordan")
    assert [c.fub_id for c in contacts] == ["101"]


async def test_voice_internal_records_calls() -> None:
    voice = build_voice_adapter()
    call_id = await voice.start_outbound_call(
        "101", "demo-assistant", {"listing_key": "RM1001", "scenario": "cool"}
    )
    record = await voice.get_call(call_id)
    assert record is not None
    assert "overpriced" in record.transcript
