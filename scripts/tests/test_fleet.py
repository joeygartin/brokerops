"""Fleet-registry validator + renderer tests (BOP-032).

The committed manifest must never hold a client identifier, `billing_model` is a closed
enum, drift is computed against the latest release tag, and the overlay merges (or the
render degrades to slug-only) at runtime.
"""

from __future__ import annotations

import textwrap

import pytest

import fleet


def test_committed_manifest_is_valid() -> None:
    result = fleet.load_manifest()  # the real infra/clients/fleet.yml
    slugs = [c.slug for c in result.clients]
    assert "demo" in slugs
    demo = next(c for c in result.clients if c.slug == "demo")
    assert demo.billing_model == "flat"
    assert demo.version == "latest"


def _manifest(entry_body: str) -> str:
    return "clients:\n  - " + textwrap.indent(entry_body, "    ").lstrip()


VALID = _manifest(
    "slug: acme\nversion: v0.1.0\nposture: hosted\nbilling_model: flat\nlast_upgraded: 2026-07-01\n"
)


def test_valid_entry_loads_with_default_onboarding() -> None:
    fleet_obj = fleet.load_manifest(VALID)
    (entry,) = fleet_obj.clients
    assert entry.onboarding.done() == 0
    assert entry.onboarding.total() == 5


def test_rejects_bad_billing_model() -> None:
    bad = VALID.replace("billing_model: flat", "billing_model: premium")
    with pytest.raises(fleet.FleetError) as e:
        fleet.load_manifest(bad)
    assert "billing_model" in str(e.value)


def test_rejects_bad_posture() -> None:
    bad = VALID.replace("posture: hosted", "posture: on-prem")
    with pytest.raises(fleet.FleetError):
        fleet.load_manifest(bad)


def test_rejects_non_opaque_slug() -> None:
    bad = VALID.replace("slug: acme", 'slug: "Acme Realty"')
    with pytest.raises(fleet.FleetError) as e:
        fleet.load_manifest(bad)
    assert "opaque" in str(e.value)


@pytest.mark.parametrize(
    "line",
    [
        "project_id: brokerops-acme",
        "tfvars: infra/clients/acme.tfvars",
        'display_name: "Acme Realty"',
        "fee_amount: 499",
    ],
)
def test_rejects_identifying_field_with_pointer_to_overlay(line: str) -> None:
    bad = VALID + textwrap.indent(line + "\n", "    ")
    with pytest.raises(fleet.FleetError) as e:
        fleet.load_manifest(bad)
    msg = str(e.value)
    assert "overlay" in msg
    assert "fleet-overlay.local.yml" in msg


def test_rejects_unknown_field() -> None:
    bad = VALID + "    region: us-west1\n"
    with pytest.raises(fleet.FleetError):
        fleet.load_manifest(bad)


@pytest.mark.parametrize("bad_version", ["v1", "release-2026-01", "latest ", "1.0.0", "vX.Y.Z"])
def test_rejects_malformed_version(bad_version: str) -> None:
    bad = VALID.replace("version: v0.1.0", f'version: "{bad_version}"')
    with pytest.raises(fleet.FleetError) as e:
        fleet.load_manifest(bad)
    assert "version" in str(e.value)


def test_accepts_latest_and_pinned_version() -> None:
    assert fleet.load_manifest(VALID.replace("version: v0.1.0", "version: latest")).clients
    assert fleet.load_manifest(VALID.replace("version: v0.1.0", "version: v12.3.4")).clients


def test_rejects_duplicate_slug() -> None:
    dup = VALID + textwrap.indent(
        "- slug: acme\n  version: v0.2.0\n  posture: hosted\n"
        "  billing_model: flat\n  last_upgraded: 2026-07-02\n",
        "  ",
    )
    with pytest.raises(fleet.FleetError) as e:
        fleet.load_manifest(dup)
    assert "duplicate slug" in str(e.value)
    assert "acme" in str(e.value)


@pytest.mark.parametrize(
    ("version", "latest", "expected"),
    [
        ("latest", "v1.2.0", "tracks latest"),
        ("v1.2.0", "v1.2.0", "current"),
        ("v1.1.0", "v1.2.0", "behind v1.2.0"),
        ("v1.1.0", None, "no releases"),
    ],
)
def test_drift(version: str, latest: str | None, expected: str) -> None:
    assert fleet.drift(version, latest) == expected


def test_render_merges_overlay_display_name_and_project() -> None:
    fleet_obj = fleet.load_manifest(VALID)
    overlay = fleet.load_overlay(
        "acme:\n  display_name: Acme Realty\n  project_id: brokerops-acme\n"
    )
    out = fleet.render(fleet_obj, overlay, latest="v0.1.0")
    assert "Acme Realty" in out
    assert "brokerops-acme" in out
    assert "current" in out


def test_render_degrades_to_slug_without_overlay() -> None:
    fleet_obj = fleet.load_manifest(VALID)
    out = fleet.render(fleet_obj, overlay={}, latest="v0.1.0")
    assert "acme" in out
    assert "—" in out  # PROJECT falls back
    assert "fall back to slug" in out


def test_render_flags_incomplete_onboarding() -> None:
    entry = VALID + "    onboarding:\n      ses_identity: true\n"
    fleet_obj = fleet.load_manifest(entry)
    out = fleet.render(fleet_obj, overlay={}, latest="v0.1.0")
    assert "1/5 ⚠" in out


def test_overlay_rejects_unknown_field() -> None:
    with pytest.raises(fleet.FleetError):
        fleet.load_overlay("acme:\n  secret_note: nope\n")
