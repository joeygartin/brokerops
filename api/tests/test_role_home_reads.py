"""The role-shaped home reads (BOP-030): the coordinator's deadline queue and the
viewer's transaction search.

Drives the real credential-free lifespan (`with TestClient(app)`) so the demo seed
and the milestone engine run exactly as `docker compose up` would, then asserts the
two new reads over the seeded portfolio (one overdue, one due-soon, one blocked).
"""

import logging
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from brokerops_api.deps import get_listing_service
from brokerops_api.main import app
from brokerops_core.models.listing import Listing, ListingMedia, ListingQuery
from brokerops_core.ports.identity import AuthError, Principal, Role
from brokerops_core.services.listing_service import ListingService


@pytest.fixture(autouse=True)
def _internal_mls(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # The search read joins the listing address, so point the MLS adapter at the
    # in-process RESO stub (the zero-credential feed the demo uses) instead of the
    # real endpoint the default base URL names.
    monkeypatch.setenv("RESO_BASE_URL", "internal")
    yield


class _StubRoleVerifier:
    """Maps the bearer ('viewer'/'operator'/'admin') to a principal of that role,
    so caller-role response filtering can be exercised (mirrors test_rbac)."""

    async def verify(self, token: str | None) -> Principal:
        if token in {"viewer", "operator", "admin"}:
            return Principal(subject=f"{token}@x.com", email=f"{token}@x.com", role=Role(token))
        raise AuthError("bad token")


def _seed(client: TestClient) -> None:
    assert client.post("/demo/seed", json={"reset": True}).status_code == 200


def test_deadline_queue_surfaces_urgent_milestones_most_urgent_first() -> None:
    with TestClient(app) as client:
        _seed(client)
        queue = client.get("/transactions/deadlines").json()

        # Exactly the three attention-worthy milestones across the portfolio — the
        # on-track ones (appraisal, financing, closing) are dropped.
        assert [row["milestone_id"] for row in queue] == [
            "MS-1001-INS",  # overdue (-2)
            "MS-1002-INS",  # due soon (+2)
            "MS-1003-FIN",  # blocked external
        ]
        by_id = {row["milestone_id"]: row for row in queue}
        assert by_id["MS-1001-INS"]["classification"] == "overdue"
        assert by_id["MS-1002-INS"]["classification"] == "due_soon"
        assert by_id["MS-1003-FIN"]["classification"] == "blocked_external"

        # Each row carries the context to link back to its transaction hub.
        assert by_id["MS-1001-INS"]["transaction_id"] == "TXN-1001"
        assert by_id["MS-1001-INS"]["listing_key"] == "RM1004"
        assert by_id["MS-1003-FIN"]["blocked_reason"] == "Awaiting lender underwriting update"


def test_deadline_queue_is_empty_with_no_transactions() -> None:
    # A fresh in-memory portfolio (nothing seeded) is a clean queue, not an error —
    # the empty-state the operator home renders proudly.
    with TestClient(app) as client:
        assert client.get("/transactions/deadlines").json() == []


def test_search_matches_listing_key_party_name_and_address() -> None:
    with TestClient(app) as client:
        _seed(client)

        def ids(query: str) -> list[str]:
            rows = client.get("/transactions/search", params={"q": query}).json()
            return [row["transaction"]["id"] for row in rows]

        assert ids("RM1010") == ["TXN-1002"]  # listing key
        assert ids("jordan") == ["TXN-1001"]  # party (contact) name, case-insensitive
        assert ids("fox hollow") == ["TXN-1002"]  # property address (joined from the listing)
        # Dana Whitfield is the listing agent on both active deals.
        assert ids("dana") == ["TXN-1001", "TXN-1002"]
        assert ids("no-such-thing") == []


def test_search_row_carries_property_address() -> None:
    with TestClient(app) as client:
        _seed(client)
        rows = client.get("/transactions/search", params={"q": "RM1004"}).json()
        assert rows[0]["property_address"] == "2204 Summit Ridge Road, Rivermouth, CA 95891"


def test_blank_search_returns_nothing() -> None:
    with TestClient(app) as client:
        _seed(client)
        assert client.get("/transactions/search", params={"q": "   "}).json() == []


class _UnreachableMLS:
    """An MLS whose reads always fail with an HTTP-layer error — a sparse or down
    feed (e.g. a live feed that 400s the demo's synthetic listing keys, exactly what
    the real RESO adapter raises via `response.raise_for_status()`)."""

    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        raise httpx.ConnectError("feed unreachable")

    async def get_listing(self, listing_key: str) -> Listing | None:
        raise httpx.ConnectError("feed unreachable")

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        raise httpx.ConnectError("feed unreachable")


def test_search_degrades_to_no_address_when_the_listing_feed_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Address is an enrichment: a listing the MLS can't serve must not 500 the
    # search — the listing-key / party-name match still stands, address just drops.
    # And the degradation is surfaced (WARNING), never silently swallowed.
    with TestClient(app) as client:
        _seed(client)
        app.dependency_overrides[get_listing_service] = lambda: ListingService(_UnreachableMLS())
        try:
            with caplog.at_level(logging.WARNING):
                rows = client.get("/transactions/search", params={"q": "RM1010"}).json()
            assert [row["transaction"]["id"] for row in rows] == ["TXN-1002"]
            assert rows[0]["property_address"] == ""
            # The feed failure is logged so a real MLS regression stays visible.
            assert any(
                "MLS listing lookup failed" in record.message and record.levelno == logging.WARNING
                for record in caplog.records
            )
        finally:
            app.dependency_overrides.clear()


class _BrokenMLS:
    """An MLS whose reads fail with a non-HTTP error — a genuine bug, not a feed
    outage. Search must NOT swallow it (fail loud, per the review gate)."""

    async def search_listings(self, query: ListingQuery) -> list[Listing]:
        raise RuntimeError("programming error")

    async def get_listing(self, listing_key: str) -> Listing | None:
        raise RuntimeError("programming error")

    async def get_listing_media(self, listing_key: str) -> list[ListingMedia]:
        raise RuntimeError("programming error")


def test_search_does_not_swallow_unexpected_listing_errors() -> None:
    # A non-HTTP error is a real bug, not a degraded feed: it must surface (500),
    # not hide behind a blank address (blanket-except was the r1 blocker).
    with TestClient(app, raise_server_exceptions=False) as client:
        _seed(client)
        app.dependency_overrides[get_listing_service] = lambda: ListingService(_BrokenMLS())
        try:
            assert client.get("/transactions/search", params={"q": "RM1010"}).status_code == 500
        finally:
            app.dependency_overrides.clear()


def test_home_reads_are_viewer_open_but_filter_party_email() -> None:
    # Both reads are viewer-open (ADR-0009: reads open, controls gated), but a
    # viewer must not receive party contact emails — CONTACT_PII, filtered at egress.
    with TestClient(app) as client:
        _seed(client)
        original = app.state.identity_verifier
        app.state.identity_verifier = _StubRoleVerifier()
        try:
            viewer = {"Authorization": "Bearer viewer"}
            operator = {"Authorization": "Bearer operator"}

            v_rows = client.get("/transactions/search", params={"q": "dana"}, headers=viewer).json()
            assert v_rows  # the read is open to a viewer
            v_emails = [p["email"] for row in v_rows for p in row["transaction"]["parties"]]
            assert v_emails and all(e in ("", "[redacted:pii]") for e in v_emails)

            o_rows = client.get(
                "/transactions/search", params={"q": "dana"}, headers=operator
            ).json()
            o_emails = [p["email"] for row in o_rows for p in row["transaction"]["parties"]]
            assert any("@" in e for e in o_emails)

            # The deadline queue carries no PII, but a viewer can read it.
            assert client.get("/transactions/deadlines", headers=viewer).status_code == 200
        finally:
            app.state.identity_verifier = original


def test_home_reads_reject_an_unauthenticated_caller_when_auth_is_on() -> None:
    with TestClient(app) as client:
        _seed(client)
        original = app.state.identity_verifier
        app.state.identity_verifier = _StubRoleVerifier()
        try:
            bad = {"Authorization": "Bearer nope"}
            assert client.get("/transactions/deadlines", headers=bad).status_code == 401
            assert (
                client.get("/transactions/search", params={"q": "x"}, headers=bad).status_code
                == 401
            )
        finally:
            app.state.identity_verifier = original
