import httpx
import pytest

from brokerops_vapi.adapter import VapiVoiceAdapter
from brokerops_vapi.stub import RECORDED_TRANSCRIPTS, create_stub_app


@pytest.fixture
def adapter() -> VapiVoiceAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://vapi.test",
        headers={"Authorization": "Bearer stub-key"},
    )
    return VapiVoiceAdapter(api_key="stub-key", base_url="http://vapi.test", client=client)


async def test_start_call_returns_id_and_get_call_maps_record(
    adapter: VapiVoiceAdapter,
) -> None:
    call_id = await adapter.start_outbound_call(
        "101", "demo-assistant", {"listing_key": "RM1001", "scenario": "hot"}
    )
    record = await adapter.get_call(call_id)
    assert record is not None
    assert record.vapi_call_id == call_id
    assert record.contact_id == "101"
    assert record.listing_key == "RM1001"
    assert record.transcript == RECORDED_TRANSCRIPTS["hot"]
    assert record.outcome == "customer-ended-call"


async def test_scenario_override_and_missing_call(adapter: VapiVoiceAdapter) -> None:
    call_id = await adapter.start_outbound_call(
        "102", "demo-assistant", {"listing_key": "RM1002", "scenario": "cool"}
    )
    record = await adapter.get_call(call_id)
    assert record is not None
    assert "overpriced" in record.transcript
    assert await adapter.get_call("call-999999") is None
