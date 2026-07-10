"""Fleet registry — the committed source of truth for which client instances exist,
on what version, with which onboarding steps done (BOP-032).

Two files, one keyed by slug:
  * infra/clients/fleet.yml               committed; OPAQUE/public fields only
  * infra/clients/fleet-overlay.local.yml gitignored; the identifying fields

The committed manifest can never hold a client identifier: the entry model forbids
unknown keys, and the known-identifying ones (real GCP project id, tfvars path,
display name, fee amount) are rejected by name and pointed at the overlay. The
upgrade driver (BOP-033) and the deploy path read overlay fields at runtime; this
module only reads and renders. See docs/fleet-registry.md for the schema.

CLI:
  python scripts/fleet.py status     render the fleet as a table (+ overlay if present)
  python scripts/fleet.py validate   validate the committed manifest, nonzero on error
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "infra" / "clients" / "fleet.yml"
OVERLAY = REPO_ROOT / "infra" / "clients" / "fleet-overlay.local.yml"

# Fields that can each encode a client identity. They live ONLY in the gitignored
# overlay, never in the committed manifest — rejected by name for a pointed message
# (the entry model's extra="forbid" would reject them anyway, less helpfully).
IDENTIFYING_KEYS = {
    "display_name",
    "name",
    "project_id",
    "gcp_project",
    "gcp_project_id",
    "tfvars",
    "tfvars_path",
    "fee",
    "fee_amount",
    "amount",
    "price",
    "monthly_fee",
    "billing_amount",
    "invoice_id",
    "invoicing_id",
}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# A pinned release tag "vX.Y.Z", or the literal "latest" for the CD-tracked demo.
VERSION_RE = re.compile(r"^(latest|v\d+\.\d+\.\d+)$")


class FleetError(Exception):
    """A manifest/overlay is malformed — surfaced as a clear CLI message."""


class OnboardingStatus(BaseModel):
    """Per-client onboarding flags — booleans only, never the underlying values."""

    model_config = ConfigDict(extra="forbid")

    ses_identity: bool = False  # SES domain identity verified
    sandbox_exit: bool = False  # SES production access (out of sandbox)
    ten_dlc: bool = False  # 10DLC / A2P SMS registration approved
    drive_sa: bool = False  # Google Drive service account provisioned
    crm_keys: bool = False  # CRM API keys pushed to Secret Manager

    def done(self) -> int:
        return sum(
            (self.ses_identity, self.sandbox_exit, self.ten_dlc, self.drive_sa, self.crm_keys)
        )

    @staticmethod
    def total() -> int:
        return 5


class ClientEntry(BaseModel):
    """One fleet member — opaque/public fields only (identity lives in the overlay)."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    version: str  # a release tag "vX.Y.Z", or "latest" for the CD-tracked demo
    posture: Literal["hosted", "client-infra"]
    billing_model: Literal["flat", "tiered"]  # design Q2: flat default, tiered optional
    last_upgraded: date
    onboarding: OnboardingStatus = Field(default_factory=OnboardingStatus)

    @field_validator("slug")
    @classmethod
    def _opaque_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                f"slug {v!r} must be an opaque lowercase token [a-z0-9-] — not a brokerage name"
            )
        return v

    @field_validator("version")
    @classmethod
    def _pinned_version(cls, v: str) -> str:
        if not VERSION_RE.match(v):
            raise ValueError(
                f"version {v!r} must be a pinned release tag 'vX.Y.Z' or 'latest' (BOP-031/ADR-0025)"
            )
        return v


class Fleet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clients: list[ClientEntry]

    @field_validator("clients")
    @classmethod
    def _unique_slugs(cls, clients: list[ClientEntry]) -> list[ClientEntry]:
        slugs = [c.slug for c in clients]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            raise ValueError(f"duplicate slug(s) {dupes} — one entry per client (slug is the key)")
        return clients


class OverlayEntry(BaseModel):
    """The gitignored, identifying counterpart to a manifest entry, keyed by slug."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    project_id: str | None = None
    tfvars: str | None = None
    billing_amount: str | None = None
    invoice_id: str | None = None


def _precheck_identifying(raw: dict[str, object]) -> None:
    entries = raw.get("clients") or []
    if not isinstance(entries, list):
        return  # shape error — let model_validate produce the message
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        bad = IDENTIFYING_KEYS & set(entry)
        if bad:
            slug = entry.get("slug", f"#{i}")
            raise FleetError(
                f"{MANIFEST.name}: entry {slug!r} carries identifying field(s) "
                f"{sorted(bad)} — these live in the gitignored overlay "
                f"({OVERLAY.name}), keyed by slug, never in the committed manifest. "
                f"See docs/fleet-registry.md."
            )


def load_manifest(text: str | None = None) -> Fleet:
    raw = yaml.safe_load(MANIFEST.read_text() if text is None else text)
    if not isinstance(raw, dict):
        raise FleetError(f"{MANIFEST.name}: top level must be a mapping with a 'clients' list")
    _precheck_identifying(raw)
    try:
        return Fleet.model_validate(raw)
    except ValidationError as e:
        raise FleetError(f"{MANIFEST.name} is invalid:\n{e}") from e


def load_overlay(text: str | None = None) -> dict[str, OverlayEntry]:
    if text is None:
        if not OVERLAY.exists():
            return {}
        text = OVERLAY.read_text()
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise FleetError(f"{OVERLAY.name}: top level must be a mapping keyed by slug")
    try:
        return {slug: OverlayEntry.model_validate(v or {}) for slug, v in raw.items()}
    except ValidationError as e:
        raise FleetError(f"{OVERLAY.name} is invalid:\n{e}") from e


def latest_release_tag() -> str | None:
    """The newest `vX.Y.Z` git tag, or None when the repo has cut no releases yet."""
    try:
        result = subprocess.run(
            ["git", "tag", "--list", "v*", "--sort=-version:refname"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    tags = result.stdout.split()
    return tags[0] if tags else None


def drift(version: str, latest: str | None) -> str:
    if version == "latest":
        return "tracks latest"
    if latest is None:
        return "no releases"
    if version == latest:
        return "current"
    return f"behind {latest}"


def render(fleet: Fleet, overlay: dict[str, OverlayEntry], latest: str | None) -> str:
    headers = [
        "CLIENT",
        "VERSION",
        "DRIFT",
        "POSTURE",
        "BILLING",
        "ONBOARD",
        "UPGRADED",
        "PROJECT",
    ]
    rows: list[list[str]] = []
    for c in fleet.clients:
        ov = overlay.get(c.slug)
        name = ov.display_name if ov and ov.display_name else c.slug
        project = ov.project_id if ov and ov.project_id else "—"
        done, total = c.onboarding.done(), OnboardingStatus.total()
        onboard = f"{done}/{total}" + ("" if done == total else " ⚠")
        rows.append(
            [
                name,
                c.version,
                drift(c.version, latest),
                c.posture,
                c.billing_model,
                onboard,
                c.last_upgraded.isoformat(),
                project,
            ]
        )
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * widths[i] for i in range(len(headers)))]
    out += ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows]
    if not overlay:
        out.append("")
        out.append(f"(no {OVERLAY.name} — CLIENT/PROJECT fall back to slug)")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    try:
        fleet = load_manifest()
        if cmd == "validate":
            load_overlay()  # surface an invalid overlay too, if one is present
            print(f"{MANIFEST.name} OK — {len(fleet.clients)} client(s).")
            return 0
        if cmd == "status":
            print(render(fleet, load_overlay(), latest_release_tag()))
            return 0
    except FleetError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"unknown command {cmd!r} (use: status | validate)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
