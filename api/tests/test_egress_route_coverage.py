"""Forget-proof coverage for caller-role egress on HTTP read routes (BOP-040).

The engine tool seam proves both directions are wired by enumerating every
registered port (``test_tool_authz_enumeration``); this is the HTTP-route sibling.
It walks every GET route the app exposes and asserts a simple, static invariant:

    if a route's response model carries a ``Pii``-marked field (directly or nested),
    that route must depend on the ``role_scrubber`` egress seam.

So a NEW read route that returns a restricted field cannot silently ship without the
caller-role filter — the same guarantee BOP-011/012 give on the tool seam, applied to
the route surface. ``response_exposes_restricted_content`` is the static detector; the
scrubber's presence in a route's dependency tree is the detectable marker (mirroring
``AUTHORIZED_MARKER`` / ``EGRESS_FILTERED_MARKER``).
"""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from brokerops_api.main import app
from brokerops_api.routes._egress import (
    response_exposes_restricted_content,
    role_scrubber,
)
from brokerops_core.models.call import CallRecord
from brokerops_core.models.contact import Contact
from brokerops_core.models.listing import Listing


def _get_routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute) and "GET" in route.methods]


def _dependency_calls(dependant: Dependant) -> list[object]:
    """Every callable in a route's (recursive) dependency tree — the detectable
    surface the scrubber-presence check walks."""
    calls: list[object] = []
    for dep in dependant.dependencies:
        calls.append(dep.call)
        calls.extend(_dependency_calls(dep))
    return calls


def _is_scrubbed(route: APIRoute) -> bool:
    return role_scrubber in _dependency_calls(route.dependant)


def _restricted_get_paths() -> set[str]:
    return {
        route.path
        for route in _get_routes()
        if response_exposes_restricted_content(route.response_model)
    }


def test_detector_flags_restricted_models_and_ignores_clean_ones() -> None:
    # The static detector is the whole test's basis, so pin its behaviour: a model
    # that owns a Pii field is flagged (contact reach, a call transcript), one with
    # none is not — otherwise the invariant below could pass vacuously.
    assert response_exposes_restricted_content(Contact) is True
    assert response_exposes_restricted_content(CallRecord) is True
    assert response_exposes_restricted_content(list[Contact]) is True  # nested container
    assert response_exposes_restricted_content(Listing) is False
    assert response_exposes_restricted_content(dict[str, object]) is False
    assert response_exposes_restricted_content(None) is False


def test_every_restricted_get_route_is_role_scrubbed() -> None:
    # The core invariant: any GET route whose response exposes a restricted field is
    # wired to the shared caller-role scrubber. A new read route that returns a
    # restricted field but forgets the seam fails HERE.
    unscrubbed = [
        route.path
        for route in _get_routes()
        if response_exposes_restricted_content(route.response_model) and not _is_scrubbed(route)
    ]
    assert unscrubbed == [], (
        f"GET routes exposing restricted content without the egress seam: {unscrubbed}"
    )


def test_the_known_restricted_surface_is_covered_and_non_vacuous() -> None:
    # Non-vacuity guard: the enumeration actually reaches the surface BOP-040 closes.
    # If a marker or a route were dropped so one of these stopped being flagged, this
    # notices it (the invariant test alone would pass vacuously for a route that no
    # longer exposes a restricted field).
    restricted = _restricted_get_paths()
    expected = {
        "/approvals",
        "/approvals/{approval_id}",
        "/messages",
        "/messages/{message_id}",
        "/audit",
        "/contacts",
        "/contacts/{contact_id}",
        "/transactions",
        "/transactions/{transaction_id}",
        "/transactions/search",
        "/calls/{call_id}",
    }
    missing = expected - restricted
    assert missing == set(), (
        f"expected restricted GET routes not flagged by the detector: {missing}"
    )
