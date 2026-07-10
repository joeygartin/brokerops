"""Fleet upgrade driver tests (BOP-033) — the pure logic: registry resolution, the
would-do table, the comment-preserving manifest/tfvars edits, the auth-aware verify's
status handling, and the stop-on-failure walk with its state report.

The terraform/cloud steps are `manual` (BOP-033) and out of scope here; everything that
decides *what* the driver does is exercised without touching cloud state.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import fleet
import fleet_upgrade as fu


# ── fixtures ─────────────────────────────────────────────────────────────────

MANIFEST_TEXT = """\
# header comment that must survive edits
clients:
  - slug: demo
    version: latest
    posture: hosted
    billing_model: flat
    last_upgraded: 2026-07-08
    onboarding:
      ses_identity: true
      sandbox_exit: true
      ten_dlc: false
      drive_sa: false
      crm_keys: false
  - slug: acme
    version: v0.1.0
    posture: client-infra
    billing_model: tiered
    last_upgraded: 2026-07-01
    onboarding:
      ses_identity: false
      sandbox_exit: false
      ten_dlc: false
      drive_sa: false
      crm_keys: false
"""


@pytest.fixture
def fleet_obj() -> fleet.Fleet:
    return fleet.load_manifest(text=MANIFEST_TEXT)


def _tfvars(tmp_path: Path, name: str, image_version: str, project: str = "brokerops-x") -> Path:
    d = tmp_path / "clients"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.tfvars"
    p.write_text(
        f'client_name = "{name}"\nproject_id  = "{project}"\nimage_version = "{image_version}"\n'
    )
    return p


# ── registry resolution ──────────────────────────────────────────────────────


def test_resolve_targets_walks_in_order_and_flags_latest_tracking(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "latest", "brokerops-demo")
    _tfvars(tmp_path, "acme", "v0.1.0", "brokerops-acme")
    monkeypatch.setattr(fu, "INFRA_DIR", tmp_path)

    targets = fu.resolve_targets(fleet_obj, {}, "v0.2.0", None)

    assert [t.slug for t in targets] == ["demo", "acme"]  # registry order
    demo, acme = targets
    assert demo.tracks_latest is True and demo.target_version == "v0.2.0"
    assert acme.tracks_latest is False
    assert demo.project == "brokerops-demo"  # read from the tfvars when no overlay
    assert demo.tfvars_rel == "clients/demo.tfvars"


def test_resolve_targets_client_filter(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "latest")
    _tfvars(tmp_path, "acme", "v0.1.0")
    monkeypatch.setattr(fu, "INFRA_DIR", tmp_path)

    targets = fu.resolve_targets(fleet_obj, {}, "v0.2.0", "acme")
    assert [t.slug for t in targets] == ["acme"]


def test_resolve_targets_unknown_client_filter_raises(fleet_obj: fleet.Fleet) -> None:
    with pytest.raises(fu.UpgradeError, match="not in"):
        fu.resolve_targets(fleet_obj, {}, "v0.2.0", "ghost")


def test_resolve_targets_missing_tfvars_raises(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "latest")  # acme's tfvars deliberately absent
    monkeypatch.setattr(fu, "INFRA_DIR", tmp_path)
    with pytest.raises(fu.UpgradeError, match="no tfvars"):
        fu.resolve_targets(fleet_obj, {}, "v0.2.0", None)


def test_resolve_targets_overlay_supplies_project_and_tfvars(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "acme", "v0.1.0")
    monkeypatch.setattr(fu, "INFRA_DIR", tmp_path)
    overlay = {
        "acme": fleet.OverlayEntry(
            display_name="Acme", project_id="real-proj-123", tfvars="infra/clients/acme.tfvars"
        )
    }
    targets = fu.resolve_targets(fleet_obj, overlay, "v0.2.0", "acme")
    assert targets[0].project == "real-proj-123"  # overlay wins over the tfvars
    assert targets[0].tfvars_rel == "clients/acme.tfvars"  # normalised relative to infra/


# ── would-do table ───────────────────────────────────────────────────────────


def test_render_table_shows_current_and_target(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "latest")
    _tfvars(tmp_path, "acme", "v0.1.0")
    monkeypatch.setattr(fu, "INFRA_DIR", tmp_path)
    table = fu.render_table(fu.resolve_targets(fleet_obj, {}, "v0.2.0", None))
    assert "CLIENT" in table and "TARGET" in table
    assert "latest" in table and "v0.1.0" in table and "v0.2.0" in table


def test_render_table_empty() -> None:
    assert "no matching clients" in fu.render_table([])


# ── manifest edit (comment-preserving) ───────────────────────────────────────


def test_update_manifest_entry_edits_only_the_target(fleet_obj: fleet.Fleet) -> None:
    upgraded = date(2026, 7, 10)
    new = fu.update_manifest_entry(MANIFEST_TEXT, "demo", "v0.2.0", upgraded)

    parsed = fleet.load_manifest(text=new)
    by_slug = {c.slug: c for c in parsed.clients}
    assert by_slug["demo"].version == "v0.2.0"
    assert by_slug["demo"].last_upgraded == upgraded
    # acme untouched
    assert by_slug["acme"].version == "v0.1.0"
    assert by_slug["acme"].last_upgraded == date(2026, 7, 1)
    # header comment survives
    assert new.startswith("# header comment that must survive edits")


def test_update_manifest_entry_missing_slug_raises() -> None:
    with pytest.raises(fu.UpgradeError, match="no entry with slug"):
        fu.update_manifest_entry(MANIFEST_TEXT, "ghost", "v0.2.0", date(2026, 7, 10))


def test_update_manifest_entry_preserves_inline_comment() -> None:
    text = "clients:\n  - slug: x\n    version: v0.1.0  # pinned\n    last_upgraded: 2026-01-01\n"
    new = fu.update_manifest_entry(text, "x", "v0.2.0", date(2026, 2, 2))
    assert "version: v0.2.0  # pinned" in new


# ── tfvars edit ──────────────────────────────────────────────────────────────


def test_update_tfvars_image_version_preserves_rest() -> None:
    text = 'client_name = "acme"\nimage_version = "v0.1.0"  # pinned\nregion = "us-west1"\n'
    new = fu.update_tfvars_image_version(text, "v0.2.0")
    assert 'image_version = "v0.2.0"  # pinned' in new
    assert 'region = "us-west1"' in new


def test_update_tfvars_image_version_absent_raises() -> None:
    with pytest.raises(fu.UpgradeError, match="no `image_version"):
        fu.update_tfvars_image_version('client_name = "acme"\n', "v0.2.0")


# ── verify (auth-aware smoke) ────────────────────────────────────────────────


@pytest.mark.parametrize("smoke_status", [200, 401])
def test_verify_accepts_200_or_401_without_token(
    monkeypatch: pytest.MonkeyPatch, smoke_status: int
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_status(url: str, token: str | None, timeout: float = 10.0) -> int:
        calls.append((url, token))
        return 200 if url.endswith(fu.READYZ_PATH) else smoke_status

    monkeypatch.setattr(fu, "_http_status", fake_status)
    fu.verify("https://api.example/", None)  # no raise
    assert calls[0][0].endswith(fu.READYZ_PATH)
    assert calls[-1][0].endswith(fu.SMOKE_PATH)


def test_verify_rejects_5xx_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fu,
        "_http_status",
        lambda url, token, timeout=10.0: 200 if url.endswith(fu.READYZ_PATH) else 503,
    )
    with pytest.raises(fu.UpgradeError, match="expected 200"):
        fu.verify("https://api.example", None)


def test_verify_with_token_requires_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fu,
        "_http_status",
        lambda url, token, timeout=10.0: 200 if url.endswith(fu.READYZ_PATH) else 401,
    )
    with pytest.raises(fu.UpgradeError, match="authenticated smoke"):
        fu.verify("https://api.example", "a-token")


def test_verify_readyz_never_ready_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fu, "READYZ_RETRIES", 2)
    monkeypatch.setattr(fu, "_http_status", lambda url, token, timeout=10.0: 0)
    monkeypatch.setattr(fu.time, "sleep", lambda _s: None)
    with pytest.raises(fu.UpgradeError, match="never returned 200"):
        fu.verify("https://api.example", None)


# ── the stop-on-failure walk ─────────────────────────────────────────────────


def _target(slug: str) -> fu.Target:
    return fu.Target(slug, "v0.1.0", "v0.2.0", f"clients/{slug}.tfvars", f"proj-{slug}", False)


def test_walk_stops_at_first_failure_and_reports_state(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted: list[str] = []

    def fake_upgrade_one(target: fu.Target, bucket: str, assume_yes: bool, upgraded: date) -> None:
        attempted.append(target.slug)
        if target.slug == "b":
            raise fu.UpgradeError("boom on b")

    monkeypatch.setattr(fu, "upgrade_one", fake_upgrade_one)
    targets = [_target("a"), _target("b"), _target("c")]

    outcomes = fu.run_walk(targets, "bucket", True, date(2026, 7, 10))

    assert attempted == ["a", "b"]  # c never attempted
    by_slug = {o.slug: o for o in outcomes}
    assert by_slug["a"].status == "upgraded"
    assert by_slug["b"].status == "failed" and "boom on b" in by_slug["b"].detail
    assert by_slug["c"].status == "not-attempted"


def test_walk_all_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fu, "upgrade_one", lambda *a, **k: None)
    outcomes = fu.run_walk([_target("a"), _target("b")], "bucket", True, date(2026, 7, 10))
    assert all(o.status == "upgraded" for o in outcomes)


# ── CLI arg validation ───────────────────────────────────────────────────────


def test_main_rejects_bad_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert fu.main(["not-a-version"]) == 2
    assert "must be a pinned release tag" in capsys.readouterr().err


def test_main_rejects_latest_as_target(capsys: pytest.CaptureFixture[str]) -> None:
    # `latest` is a manifest tracking mode, never a fleet-upgrade target (BOP-031/ADR-0025).
    assert fu.main(["latest"]) == 2
    assert "never 'latest'" in capsys.readouterr().err


def test_main_dry_run_no_bucket_prints_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Exercises the runnable-without-cloud path: the would-do table prints and nothing is
    # planned/applied when TF_STATE_BUCKET is unset. Runs against the real committed registry.
    monkeypatch.delenv("TF_STATE_BUCKET", raising=False)
    assert fu.main(["v0.9.9", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "CLIENT" in out and "v0.9.9" in out
    assert "plans skipped" in out
