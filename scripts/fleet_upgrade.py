"""Fleet upgrade driver — plan → apply → verify per client, stop on first failure (BOP-033).

Upgrading N client instances must be one command, not N remembered rituals. This driver
walks the BOP-032 fleet registry (infra/clients/fleet.yml, plus the gitignored overlay
for the identifying fields) and, per client in registry order:

  1. resolves the client's tfvars + GCP project (overlay, or convention),
  2. `terraform plan` at the target version (surfaced for an interactive confirm),
  3. `terraform apply`, pinning `image_version=<VERSION>` — the same knob `make deploy`
     sets (ADR-0025); the manifest, not the tfvars, is the fleet's source of truth,
  4. verify: `/readyz` 200 + a cheap auth-aware smoke on a protected route,
  5. record the pin in the manifest (version + last_upgraded).

The walk stops on the first failure and prints a state report: which clients upgraded,
which failed, which were never attempted. The same driver serves the client-infra tier
with different credentials (the operator's active gcloud context). Marked `manual`
autonomy (BOP-033) — the verify surface is live cloud state.

The target <VERSION> is always a pinned release tag `vX.Y.Z` (BOP-031/ADR-0025) — a fleet
upgrade must be reproducible, so `latest` (a manifest/CD tracking mode) is rejected as a
target even though the manifest's own `version` field may record it for the demo.

Pin mechanism, reconciled with the seam:
  * The deploy is pinned via `-var image_version=<VERSION>` (mirrors `make deploy`), so
    a fleet upgrade never rewrites a committed file to change what it deploys.
  * The **manifest** entry (version + last_upgraded) is updated on success — the registry
    is the source of truth for what each client runs (BOP-032).
  * A client's tfvars `image_version` is synced to <VERSION> too, so other tooling
    (a raw `terraform apply` with no -var) stays consistent — EXCEPT entries that track
    `latest` (the CD-rolled demo, ADR-0025): those keep tracking `latest` in tfvars while
    the manifest records the one-off pin.

CLI:
  python scripts/fleet_upgrade.py <VERSION> [--client SLUG] [--yes] [--dry-run]
  make fleet-upgrade VERSION=v0.2.0            # all clients
  make fleet-upgrade VERSION=v0.2.0 CLIENT=demo --yes

Env:
  TF_STATE_BUCKET   GCS bucket for terraform state (per BOP-031 deploy path). Required for
                    a real run and for --dry-run plans; the would-do table prints without it.
  FLEET_SMOKE_TOKEN optional session bearer for a truly authenticated smoke (200 required
                    when set). Without it the smoke accepts 200/401 on the protected route.

See docs/migration-discipline.md for the migration + rollback rules this driver assumes.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import fleet

INFRA_DIR = fleet.REPO_ROOT / "infra"

# The protected read used as the authenticated smoke: it requires a principal when auth
# is enabled (routes/approvals.py), so — with no token — a live auth deploy returns 401
# and an auth-off deploy returns 200. Either proves the app + auth stack booted and routed.
SMOKE_PATH = "/approvals"
READYZ_PATH = "/readyz"

READYZ_RETRIES = 12  # ~a Cloud Run revision's cold-start window
READYZ_DELAY_S = 5

# A fleet upgrade TARGET must be a reproducible pinned release tag (BOP-031/ADR-0025) —
# stricter than the manifest's own `version` field (fleet.VERSION_RE), which also permits
# `latest` for the CD-tracked demo. `latest` is a *tracking mode* recorded in the manifest,
# never an upgrade target: "upgrade the fleet to latest" is unreproducible by definition.
TARGET_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")


class UpgradeError(Exception):
    """A step failed for one client — caught by the walk, which stops and reports state."""


@dataclass
class Target:
    """One resolved fleet member ready to upgrade."""

    slug: str
    current_version: str
    target_version: str
    tfvars_rel: str  # path to the tfvars, relative to infra/ (terraform runs with -chdir=infra)
    project: str  # display only — the real project id lives in the gitignored overlay
    tracks_latest: bool  # tfvars image_version is "latest" → don't rewrite it, only the manifest


# ── registry resolution ──────────────────────────────────────────────────────


def _read_tfvars_image_version(tfvars_abs: Path) -> str | None:
    """The `image_version = "..."` value in a tfvars file, or None if absent."""
    if not tfvars_abs.exists():
        return None
    m = re.search(r'^\s*image_version\s*=\s*"([^"]*)"', tfvars_abs.read_text(), re.MULTILINE)
    return m.group(1) if m else None


def resolve_targets(
    fleet_obj: fleet.Fleet,
    overlay: dict[str, fleet.OverlayEntry],
    target_version: str,
    client_filter: str | None,
) -> list[Target]:
    """Walk the manifest in order → the concrete per-client upgrade plan.

    tfvars/project come from the overlay when present, else the convention
    (`infra/clients/<slug>.tfvars`, project from that file / the slug). Raises if a
    `--client` filter matches nothing, or a client has no resolvable tfvars.
    """
    if client_filter is not None:
        slugs = {c.slug for c in fleet_obj.clients}
        if client_filter not in slugs:
            raise UpgradeError(
                f"--client {client_filter!r} is not in {fleet.MANIFEST.name} "
                f"(known: {', '.join(sorted(slugs)) or 'none'})"
            )

    targets: list[Target] = []
    for c in fleet_obj.clients:
        if client_filter is not None and c.slug != client_filter:
            continue
        ov = overlay.get(c.slug)
        tfvars_rel = _infra_relative(ov.tfvars) if ov and ov.tfvars else f"clients/{c.slug}.tfvars"
        tfvars_abs = INFRA_DIR / tfvars_rel
        if not tfvars_abs.exists():
            raise UpgradeError(
                f"client {c.slug!r}: no tfvars at {tfvars_abs} — add it, or point the "
                f"overlay's `tfvars` at the right file ({fleet.OVERLAY.name})"
            )
        project = ov.project_id if ov and ov.project_id else _read_project_id(tfvars_abs) or c.slug
        targets.append(
            Target(
                slug=c.slug,
                current_version=c.version,
                target_version=target_version,
                tfvars_rel=tfvars_rel,
                project=project,
                tracks_latest=_read_tfvars_image_version(tfvars_abs) == "latest",
            )
        )
    return targets


def _infra_relative(tfvars: str) -> str:
    """Normalise an overlay tfvars path to be relative to infra/ (terraform -chdir=infra)."""
    p = tfvars.strip()
    for prefix in ("infra/", "./infra/"):
        if p.startswith(prefix):
            return p[len(prefix) :]
    return p


def _read_project_id(tfvars_abs: Path) -> str | None:
    m = re.search(r'^\s*project_id\s*=\s*"([^"]*)"', tfvars_abs.read_text(), re.MULTILINE)
    return m.group(1) if m else None


# ── the would-do table ───────────────────────────────────────────────────────


def render_table(targets: list[Target]) -> str:
    headers = ["CLIENT", "PROJECT", "CURRENT", "TARGET", "TFVARS"]
    rows = [[t.slug, t.project, t.current_version, t.target_version, t.tfvars_rel] for t in targets]
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    out = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    out += ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)) for r in rows]
    if not rows:
        out.append("(no matching clients)")
    return "\n".join(out)


# ── manifest / tfvars surgical edits (comment-preserving) ─────────────────────

_ENTRY_RE = re.compile(r"^(?P<indent>\s*)-\s+slug:\s*(?P<slug>[^\s#]+)")
_VERSION_RE = re.compile(r"^(?P<i>\s*)version:\s*(?P<v>[^\s#]+)(?P<rest>.*)$")
_UPGRADED_RE = re.compile(r"^(?P<i>\s*)last_upgraded:\s*(?P<v>[^\s#]+)(?P<rest>.*)$")


def update_manifest_entry(text: str, slug: str, version: str, upgraded: date) -> str:
    """Return `text` with `slug`'s `version` and `last_upgraded` replaced in place.

    A targeted line edit (not a YAML round-trip) so the manifest's header comments and the
    other entries survive byte-for-byte. Raises if the entry, or either field, is missing.
    """
    lines = text.splitlines(keepends=True)
    start = _entry_start(lines, slug)
    if start is None:
        raise UpgradeError(f"{fleet.MANIFEST.name}: no entry with slug {slug!r} to update")
    end = _entry_end(lines, start)

    did_version = did_upgraded = False
    for i in range(start, end):
        body, nl = _split_newline(lines[i])
        vm = _VERSION_RE.match(body)
        if vm and not did_version:
            lines[i] = f"{vm['i']}version: {version}{vm['rest']}{nl}"
            did_version = True
            continue
        um = _UPGRADED_RE.match(body)
        if um and not did_upgraded:
            lines[i] = f"{um['i']}last_upgraded: {upgraded.isoformat()}{um['rest']}{nl}"
            did_upgraded = True
    if not (did_version and did_upgraded):
        missing = ", ".join(
            f for f, ok in (("version", did_version), ("last_upgraded", did_upgraded)) if not ok
        )
        raise UpgradeError(f"{fleet.MANIFEST.name}: entry {slug!r} is missing field(s): {missing}")
    return "".join(lines)


def update_tfvars_image_version(text: str, version: str) -> str:
    """Return `text` with `image_version = "..."` set to `version` (comment preserved)."""
    new, n = re.subn(
        r'(^\s*image_version\s*=\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        raise UpgradeError('tfvars has no `image_version = "..."` line to pin')
    return new


def _entry_start(lines: list[str], slug: str) -> int | None:
    for i, line in enumerate(lines):
        m = _ENTRY_RE.match(line)
        if m and m["slug"] == slug:
            return i
    return None


def _entry_end(lines: list[str], start: int) -> int:
    marker_indent = len(_ENTRY_RE.match(lines[start]).group("indent"))  # type: ignore[union-attr]
    for i in range(start + 1, len(lines)):
        m = _ENTRY_RE.match(lines[i])
        if m and len(m["indent"]) <= marker_indent:
            return i
    return len(lines)


def _split_newline(line: str) -> tuple[str, str]:
    return (line[:-1], "\n") if line.endswith("\n") else (line, "")


# ── terraform + verify ───────────────────────────────────────────────────────


def _run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command in the repo root, streaming to the terminal unless `capture`."""
    return subprocess.run(
        cmd,
        cwd=fleet.REPO_ROOT,
        text=True,
        check=True,
        capture_output=capture,
    )


def tf_init(bucket: str, slug: str) -> None:
    _run(
        [
            "terraform",
            f"-chdir={INFRA_DIR}",
            "init",
            "-reconfigure",
            "-input=false",
            f"-backend-config=bucket={bucket}",
            f"-backend-config=prefix=brokerops/{slug}",
        ]
    )


def tf_plan(target: Target, out_file: str | None) -> None:
    cmd = [
        "terraform",
        f"-chdir={INFRA_DIR}",
        "plan",
        "-input=false",
        f"-var-file={target.tfvars_rel}",
        f"-var=image_version={target.target_version}",
    ]
    if out_file is not None:
        cmd.append(f"-out={out_file}")
    _run(cmd)


def tf_apply(out_file: str) -> None:
    _run(["terraform", f"-chdir={INFRA_DIR}", "apply", "-input=false", out_file])


def tf_output(name: str) -> str:
    result = _run(["terraform", f"-chdir={INFRA_DIR}", "output", "-raw", name], capture=True)
    return result.stdout.strip()


def _http_status(url: str, token: str | None, timeout: float = 10.0) -> int:
    """GET `url`, returning the HTTP status (an HTTPError's status counts, e.g. 401). 0 on
    a connection-level failure (no response at all)."""
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (urllib.error.URLError, OSError):
        return 0


def verify(api_url: str, token: str | None) -> None:
    """Assert the upgraded api serves: /readyz 200 (with warmup retries) + an auth-aware
    smoke on a protected route. Raises UpgradeError on any failure."""
    base = api_url.rstrip("/")
    for attempt in range(1, READYZ_RETRIES + 1):
        if _http_status(f"{base}{READYZ_PATH}", None) == 200:
            break
        if attempt == READYZ_RETRIES:
            raise UpgradeError(
                f"{base}{READYZ_PATH} never returned 200 after {READYZ_RETRIES} tries"
            )
        print(f"    /readyz not ready (try {attempt}/{READYZ_RETRIES}) — waiting {READYZ_DELAY_S}s")
        time.sleep(READYZ_DELAY_S)

    status = _http_status(f"{base}{SMOKE_PATH}", token)
    if token:
        if status != 200:
            raise UpgradeError(
                f"authenticated smoke {SMOKE_PATH} returned {status}, expected 200 "
                f"(check FLEET_SMOKE_TOKEN is a current session bearer)"
            )
    elif status not in (200, 401):
        raise UpgradeError(
            f"smoke {SMOKE_PATH} returned {status}, expected 200 (auth off) or 401 "
            f"(auth on) — set FLEET_SMOKE_TOKEN for an authenticated 200"
        )


# ── the walk ─────────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    slug: str
    status: str  # "upgraded" | "failed" | "not-attempted"
    detail: str = ""


def _write_manifest_pin(target: Target, upgraded: date) -> None:
    """Persist the pin: manifest (always) + tfvars (unless the client tracks `latest`)."""
    text = fleet.MANIFEST.read_text()
    new_text = update_manifest_entry(text, target.slug, target.target_version, upgraded)
    fleet.load_manifest(text=new_text)  # fail loud before we write an invalid manifest
    fleet.MANIFEST.write_text(new_text)

    if not target.tracks_latest:
        tfvars_abs = INFRA_DIR / target.tfvars_rel
        tfvars_abs.write_text(
            update_tfvars_image_version(tfvars_abs.read_text(), target.target_version)
        )


def upgrade_one(target: Target, bucket: str, assume_yes: bool, upgraded: date) -> None:
    """Init → plan → confirm → apply → verify → record the pin. Raises on any failure."""
    print(
        f"\n=== {target.slug} ({target.project}): {target.current_version} → {target.target_version} ==="
    )
    tf_init(bucket, target.slug)

    plan_file = str(INFRA_DIR / f".fleet-{target.slug}.tfplan")
    try:
        tf_plan(target, plan_file)
        if not assume_yes and not _confirm(f"apply this plan to {target.slug}?"):
            raise UpgradeError(f"{target.slug}: apply declined at the confirm prompt")
        tf_apply(plan_file)
    finally:
        _quiet_unlink(plan_file)

    api_url = tf_output("api_url")
    print(f"    verifying {api_url} …")
    verify(api_url, os.environ.get("FLEET_SMOKE_TOKEN"))

    _write_manifest_pin(target, upgraded)
    print(f"    ✓ {target.slug} upgraded to {target.target_version} and recorded in the manifest")


def run_walk(targets: list[Target], bucket: str, assume_yes: bool, upgraded: date) -> list[Outcome]:
    """Upgrade each target in order, stopping at the first failure. Returns per-client
    outcomes (upgraded / failed / not-attempted) for the state report."""
    outcomes: list[Outcome] = []
    for i, t in enumerate(targets):
        try:
            upgrade_one(t, bucket, assume_yes, upgraded)
            outcomes.append(Outcome(t.slug, "upgraded"))
        except (UpgradeError, subprocess.CalledProcessError) as e:
            outcomes.append(
                Outcome(t.slug, "failed", str(e).splitlines()[0] if str(e) else type(e).__name__)
            )
            for later in targets[i + 1 :]:
                outcomes.append(Outcome(later.slug, "not-attempted"))
            break
    return outcomes


def _report(outcomes: list[Outcome]) -> None:
    print("\n── fleet upgrade report ─────────────────────────")
    symbol = {"upgraded": "✓", "failed": "✗", "not-attempted": "·"}
    for o in outcomes:
        line = f"  {symbol.get(o.status, '?')} {o.slug}: {o.status}"
        print(f"{line} — {o.detail}" if o.detail else line)


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _quiet_unlink(path: str) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="fleet_upgrade.py",
        description="Upgrade fleet clients to a pinned release: plan → apply → verify, stop on failure.",
    )
    p.add_argument(
        "version",
        help="target release tag 'vX.Y.Z' (a pinned, reproducible release — not 'latest')",
    )
    p.add_argument(
        "--client", help="only upgrade this slug (default: every client in registry order)"
    )
    p.add_argument(
        "--yes", action="store_true", help="skip the per-client confirm (non-interactive)"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the would-do table and plans only; apply nothing",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not TARGET_VERSION_RE.match(args.version):
        print(
            f"version {args.version!r} must be a pinned release tag 'vX.Y.Z' — a fleet "
            f"upgrade targets a reproducible release, never 'latest' (BOP-031/ADR-0025)",
            file=sys.stderr,
        )
        return 2

    try:
        fleet_obj = fleet.load_manifest()
        overlay = fleet.load_overlay()
        targets = resolve_targets(fleet_obj, overlay, args.version, args.client)
    except fleet.FleetError as e:
        print(str(e), file=sys.stderr)
        return 1
    except UpgradeError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(render_table(targets))
    if not targets:
        return 0

    bucket = os.environ.get("TF_STATE_BUCKET")

    if args.dry_run:
        if not bucket:
            print("\n(dry run: no TF_STATE_BUCKET → plans skipped; the table above is the preview)")
            return 0
        print("\n(dry run: terraform plan per client — applies nothing)\n")
        for t in targets:
            print(f"=== plan: {t.slug} → {t.target_version} ===")
            tf_init(bucket, t.slug)
            tf_plan(t, None)
        return 0

    if not bucket:
        print("\nset TF_STATE_BUCKET to run a real upgrade (see .env.example)", file=sys.stderr)
        return 1

    upgraded = date.today()
    outcomes = run_walk(targets, bucket, args.yes, upgraded)
    _report(outcomes)
    return 0 if all(o.status == "upgraded" for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
