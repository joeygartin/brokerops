"""Contract tests for the mock RESO Web API's OData surface.

These pin the exact subset consumers rely on. A live RESO endpoint must pass
the same shapes for the swap to be a base-URL change.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from brokerops_mls_reso.server import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def values(response: Any) -> list[dict[str, Any]]:
    body = response.json()
    assert "@odata.context" in body
    return list(body["value"])


def test_all_properties_returns_full_seed(client: TestClient) -> None:
    response = client.get("/odata/Property")
    assert response.status_code == 200
    assert len(values(response)) == 12


def test_filter_eq_on_status(client: TestClient) -> None:
    response = client.get("/odata/Property", params={"$filter": "StandardStatus eq 'Active'"})
    records = values(response)
    assert len(records) == 8
    assert all(r["StandardStatus"] == "Active" for r in records)


def test_filter_gt_lt_combined_with_and(client: TestClient) -> None:
    response = client.get(
        "/odata/Property",
        params={"$filter": "ListPrice gt 400000 and ListPrice lt 600000"},
    )
    records = values(response)
    assert records
    assert all(400000 < r["ListPrice"] < 600000 for r in records)


def test_filter_or_with_parentheses(client: TestClient) -> None:
    response = client.get(
        "/odata/Property",
        params={
            "$filter": "(StandardStatus eq 'Pending' or StandardStatus eq 'Closed')"
            " and ListPrice lt 500000"
        },
    )
    records = values(response)
    assert {r["ListingKey"] for r in records} == {"RM1010", "RM1007", "RM1012"}


def test_invalid_filter_returns_400(client: TestClient) -> None:
    response = client.get("/odata/Property", params={"$filter": "ListPrice between 1 and 2"})
    assert response.status_code == 400


def test_select_projects_fields(client: TestClient) -> None:
    response = client.get(
        "/odata/Property",
        params={"$select": "ListingKey,ListPrice", "$top": "1"},
    )
    records = values(response)
    assert len(records) == 1
    assert set(records[0]) == {"ListingKey", "ListPrice"}


def test_orderby_desc_with_top_and_skip(client: TestClient) -> None:
    first_two = values(
        client.get("/odata/Property", params={"$orderby": "ListPrice desc", "$top": "2"})
    )
    assert [r["ListingKey"] for r in first_two] == ["RM1009", "RM1006"]
    skipped = values(
        client.get(
            "/odata/Property",
            params={"$orderby": "ListPrice desc", "$top": "2", "$skip": "2"},
        )
    )
    assert [r["ListingKey"] for r in skipped] == ["RM1004", "RM1002"]


def test_single_property_by_key(client: TestClient) -> None:
    response = client.get("/odata/Property('RM1001')")
    assert response.status_code == 200
    body = response.json()
    assert body["ListingKey"] == "RM1001"
    assert body["UnparsedAddress"].startswith("412 Alder Court")


def test_single_property_unknown_key_404(client: TestClient) -> None:
    assert client.get("/odata/Property('RM9999')").status_code == 404


def test_media_filtered_by_resource_record_key(client: TestClient) -> None:
    response = client.get(
        "/odata/Media",
        params={"$filter": "ResourceRecordKey eq 'RM1001'", "$orderby": "Order"},
    )
    records = values(response)
    assert [r["MediaKey"] for r in records] == ["RM1001-M1", "RM1001-M2", "RM1001-M3"]
