from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from brokerops_api.deps import get_crm_port
from brokerops_api.main import app
from brokerops_followupboss.adapter import FUBCRMAdapter
from brokerops_followupboss.stub import create_stub_app


def _stub_crm() -> FUBCRMAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    fub_client = httpx.AsyncClient(
        transport=transport, base_url="http://fub.test", auth=("stub-key", "")
    )
    return FUBCRMAdapter(api_key="stub-key", base_url="http://fub.test", client=fub_client)


crm = _stub_crm()
client = TestClient(app)


@pytest.fixture(autouse=True)
def _wire_overrides() -> Iterator[None]:
    app.dependency_overrides[get_crm_port] = lambda: crm
    yield
    app.dependency_overrides.clear()


def test_search_contacts_via_port() -> None:
    response = client.get("/contacts", params={"q": "jordan"})
    assert response.status_code == 200
    contacts = response.json()
    assert [c["fub_id"] for c in contacts] == ["101"]
    assert contacts[0]["name"] == "Jordan Pike"


def test_get_contact_and_missing() -> None:
    assert client.get("/contacts/102").json()["role"] == "Hot Prospect"
    assert client.get("/contacts/999999").status_code == 404
