"""Client offboarding path — export + confirm-gated destroy + registry mark (BOP-036).

A client leaving must be a documented, tested path, not an improvisation. Given a
registry slug this driver:

  1. resolves the client (manifest + overlay / convention),
  2. exports a full `pg_dump` + documents FileRef manifest (CSV + JSON) + audit-ledger
     (`mutation_records`) JSON into a dated archive,
  3. delivers the archive to a local path or `gs://` bucket,
  4. scans the archive so no secrets ride out with the data,
  5. confirm-gates `terraform destroy` for that client (hosted / A1),
  6. verifies Secret Manager entries for the client are gone (not assumed),
  7. marks the fleet registry entry `offboarded_at` (kept for history, never deleted).

B-tier (`posture: client-infra`, or `--mode client-infra`): the client keeps their
project and data; we only export (optional), revoke our operator access surface, and
mark the registry. Destroy is refused.

Manual autonomy (cloud state) — same hand-run posture as BOP-013/033. Pure logic
(export assembly, secret scan, registry edit, resolve) is unit-tested; the live
deploy→seed→offboard→verify drill is documented in docs/OFFBOARDING.md.

CLI:
  scripts/offboard_client.sh <SLUG> --dest <path|gs://bucket/prefix> [flags]
  uv run python scripts/offboard_client.py <SLUG> --dest ./exports [--yes] [--dry-run]
  make offboard CLIENT=demo DEST=./exports/demo OFFBOARD_ARGS='--export-only'

Env:
  CLIENT_DATABASE_URL / DATABASE_URL   Postgres DSN for the export (owner role preferred
                                      for a complete dump). Required for a real export.
  TF_STATE_BUCKET                     GCS bucket for terraform state (required for destroy).
  OFFBOARD_PG_DUMP                    Override pg_dump binary (default: pg_dump on PATH,
                                      else `docker run --rm postgres:16-alpine pg_dump`).
  OFFBOARD_SCHEDULER_LOCATION         Override the Cloud Scheduler probe region (default:
                                      derived from the client's tfvars `region`). Set only
                                      if a job was relocated off `var.region`.
  OFFBOARD_SECRET_PROJECTS            Comma-separated extra GCP projects to scan for
                                      `brokerops-<slug>-*` secrets (deploy project always
                                      scanned).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import fleet
import fleet_upgrade as fu

INFRA_DIR = fu.INFRA_DIR

# Terraform's declared default for `var.region` (infra/variables.tf). When a client's
# tfvars omits `region`, Terraform provisions the scheduler job here — so this is the
# authoritative fallback for the cleanup probe, not a guess.
TERRAFORM_DEFAULT_REGION = "us-west1"

# Secret-shaped patterns that must never appear in the delivered archive (AC).
# Deliberately conservative: a hit fails the offboard so an operator investigates.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    (
        "pem-private-key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "postgres-url-with-password",
        re.compile(rb"postgres(?:ql)?://[^:\s/]+:[^@\s/]+@"),
    ),
    (
        "aws-access-key-id",
        re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    ),
    (
        "generic-api-key-assignment",
        re.compile(
            rb"(?i)(?:api[_-]?key|secret[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"
        ),
    ),
    (
        "bearer-token",
        re.compile(rb"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    ),
]


class OffboardError(Exception):
    """A step failed — surfaced as a clear CLI message, nonzero exit."""


@dataclass
class ClientTarget:
    """One resolved fleet member ready to offboard."""

    slug: str
    posture: str  # hosted | client-infra
    project: str
    tfvars_rel: str | None  # None when mark-only / no tfvars (post-teardown recovery)
    already_offboarded: date | None


# ── resolution ───────────────────────────────────────────────────────────────


def resolve_client(
    fleet_obj: fleet.Fleet,
    overlay: dict[str, fleet.OverlayEntry],
    slug: str,
    *,
    require_tfvars: bool = True,
) -> ClientTarget:
    entry = next((c for c in fleet_obj.clients if c.slug == slug), None)
    if entry is None:
        known = ", ".join(sorted(c.slug for c in fleet_obj.clients)) or "none"
        raise OffboardError(f"slug {slug!r} is not in {fleet.MANIFEST.name} (known: {known})")

    ov = overlay.get(slug)
    tfvars_rel = fu._infra_relative(ov.tfvars) if ov and ov.tfvars else f"clients/{slug}.tfvars"
    tfvars_abs = INFRA_DIR / tfvars_rel
    if not tfvars_abs.exists():
        if require_tfvars:
            raise OffboardError(
                f"client {slug!r}: no tfvars at {tfvars_abs} — add it, or point the overlay's "
                f"`tfvars` at the right file ({fleet.OVERLAY.name})"
            )
        project = (ov.project_id if ov and ov.project_id else None) or slug
        return ClientTarget(
            slug=slug,
            posture=entry.posture,
            project=project,
            tfvars_rel=None,
            already_offboarded=entry.offboarded_at,
        )
    project = ov.project_id if ov and ov.project_id else fu._read_project_id(tfvars_abs) or slug
    return ClientTarget(
        slug=slug,
        posture=entry.posture,
        project=project,
        tfvars_rel=tfvars_rel,
        already_offboarded=entry.offboarded_at,
    )


# ── database URL helpers ─────────────────────────────────────────────────────


def resolve_database_url(explicit: str | None) -> str:
    """Prefer --database-url, then CLIENT_DATABASE_URL, then DATABASE_URL."""
    url = (
        explicit or os.environ.get("CLIENT_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    ).strip()
    if not url:
        raise OffboardError(
            "set CLIENT_DATABASE_URL (or DATABASE_URL / --database-url) to the client's "
            "Postgres DSN before exporting — owner role preferred for a complete dump"
        )
    return url


def redact_database_url(url: str) -> str:
    """Render a DSN safe for logs (password stripped)."""
    try:
        p = urlparse(url)
        if p.password is None:
            return url
        user = p.username or ""
        host = p.hostname or ""
        netloc = f"{user}:***@{host}"
        if p.port:
            netloc += f":{p.port}"
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:  # noqa: BLE001 — best-effort redaction only
        return "<redacted-dsn>"


def strip_dsn_password(url: str) -> str:
    """Return the DSN with any password removed — auth travels via PGPASSWORD, never argv.

    A password embedded in a command-line DSN leaks through process listings (`ps`,
    `/proc/<pid>/cmdline`). libpq reads the password from PGPASSWORD (set by `_dsn_env`)
    when the URI omits it, so every `pg_dump`/`psql` argv passes through this first and no
    command string ever carries secret material.
    """
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return url
    if p.password is None:
        return url
    user = p.username or ""
    host = p.hostname or ""
    if host:
        netloc = f"{user}@{host}" if user else host
    else:
        netloc = user
    if p.port:
        netloc += f":{p.port}"
    return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))


# ── export ───────────────────────────────────────────────────────────────────


def _client_tool(binary: str) -> tuple[str, list[str]]:
    """Return ('host'|'docker', argv-prefix) for pg_dump/psql.

    Host binaries preferred. Dockerized postgres:16 client is the no-install fallback
    (Docker Desktop on macOS cannot see host paths/network the same way as Linux, so
    callers must mount the work dir and rewrite localhost DSNs — see _dockerize_dsn).
    """
    override_key = "OFFBOARD_PG_DUMP" if binary == "pg_dump" else "OFFBOARD_PSQL"
    override = os.environ.get(override_key)
    if override:
        return "host", override.split()
    host = shutil.which(binary)
    if host:
        return "host", [host]
    if shutil.which("docker"):
        return "docker", [
            "docker",
            "run",
            "--rm",
            "-e",
            "PGPASSWORD",
            "postgres:16-alpine",
            binary,
        ]
    raise OffboardError(
        f"{binary} not on PATH and docker unavailable — install libpq client tools or docker, "
        f"or set {override_key} to a {binary} binary"
    )


def _dsn_env(url: str) -> dict[str, str]:
    env = os.environ.copy()
    # libpq picks password from the URL; also surface PGPASSWORD for dockerized clients.
    try:
        p = urlparse(url)
        if p.password:
            env["PGPASSWORD"] = p.password
    except Exception:  # noqa: BLE001
        pass
    return env


def _dockerize_dsn(url: str) -> str:
    """Point a localhost DSN at host.docker.internal so a containerized client can reach it.

    The password is intentionally dropped here (and by strip_dsn_password at every call
    site): the container authenticates via PGPASSWORD (`-e PGPASSWORD`), never a DSN on the
    command line.
    """
    try:
        p = urlparse(url)
        host = p.hostname or ""
        if host in ("127.0.0.1", "localhost", "::1"):
            user = p.username or ""
            netloc = f"{user}@host.docker.internal" if user else "host.docker.internal"
            if p.port:
                netloc += f":{p.port}"
            return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:  # noqa: BLE001
        pass
    return strip_dsn_password(url)


def run_pg_dump(database_url: str, out_file: Path) -> None:
    """Full custom-format dump of the client database (restorable via pg_restore)."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    mode, prefix = _client_tool("pg_dump")
    env = _dsn_env(database_url)

    if mode == "host":
        cmd = [
            *prefix,
            "--format=custom",
            "--no-owner",
            "--no-acl",
            f"--file={out_file}",
            "--dbname",
            strip_dsn_password(database_url),  # password via PGPASSWORD, never argv
        ]
        try:
            subprocess.run(cmd, check=True, env=env, text=True)
        except FileNotFoundError as e:
            raise OffboardError(f"pg_dump failed to start: {e}") from e
        except subprocess.CalledProcessError as e:
            raise OffboardError(
                f"pg_dump failed (exit {e.returncode}) for {redact_database_url(database_url)}"
            ) from e
    else:
        # Mount the output directory; write inside the container; DSN via host.docker.internal.
        out_dir = out_file.parent.resolve()
        container_file = f"/out/{out_file.name}"
        dsn = strip_dsn_password(
            _dockerize_dsn(database_url)
        )  # password via PGPASSWORD, never argv
        cmd = [
            "docker",
            "run",
            "--rm",
            "-e",
            "PGPASSWORD",
            "-v",
            f"{out_dir}:/out",
            "postgres:16-alpine",
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            f"--file={container_file}",
            "--dbname",
            dsn,
        ]
        try:
            subprocess.run(cmd, check=True, env=env, text=True)
        except FileNotFoundError as e:
            raise OffboardError(f"pg_dump (docker) failed to start: {e}") from e
        except subprocess.CalledProcessError as e:
            raise OffboardError(
                f"pg_dump failed (exit {e.returncode}) for {redact_database_url(database_url)}"
            ) from e

    if not out_file.exists() or out_file.stat().st_size == 0:
        raise OffboardError("pg_dump produced an empty dump file")


def _psql_copy_csv(database_url: str, sql: str) -> str:
    """Run `COPY (<sql>) TO STDOUT WITH CSV HEADER` via psql; return CSV text."""
    copy_sql = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
    mode, prefix = _client_tool("psql")
    env = _dsn_env(database_url)

    if mode == "host":
        cmd = [
            *prefix,
            "--no-psqlrc",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            strip_dsn_password(database_url),  # password via PGPASSWORD, never argv
            "-c",
            copy_sql,
        ]
    else:
        dsn = strip_dsn_password(
            _dockerize_dsn(database_url)
        )  # password via PGPASSWORD, never argv
        cmd = [
            "docker",
            "run",
            "--rm",
            "-e",
            "PGPASSWORD",
            "postgres:16-alpine",
            "psql",
            "--no-psqlrc",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            dsn,
            "-c",
            copy_sql,
        ]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise OffboardError(f"psql failed to start: {e}") from e
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip().splitlines()
        detail = err[-1] if err else f"exit {e.returncode}"
        raise OffboardError(f"psql export failed: {detail}") from e
    return result.stdout


DOCUMENTS_SQL = """
SELECT id, tenant_id, transaction_id, milestone_id, kind, title,
       file::text AS file_ref, uploaded_by, created_at
FROM documents
ORDER BY created_at NULLS LAST, id
""".strip()

AUDIT_SQL = """
SELECT id, tenant_id, workflow_run_id, workflow, transaction_id, tool, integration,
       args::text AS args, approval_id, actor, outcome, external_id, error, created_at
FROM mutation_records
ORDER BY created_at NULLS LAST, id
""".strip()


def export_documents_manifest(database_url: str, dest_dir: Path) -> tuple[Path, Path]:
    """Write documents FileRefs as CSV + JSON (pointers only — bytes live in Drive)."""
    csv_text = _psql_copy_csv(database_url, DOCUMENTS_SQL)
    csv_path = dest_dir / "documents_manifest.csv"
    csv_path.write_text(csv_text, encoding="utf-8")

    rows = list(_csv_rows(csv_text))
    # Expand file_ref JSON column into a nested object for the JSON form.
    for row in rows:
        raw = row.get("file_ref") or row.get("file") or "{}"
        try:
            row["file"] = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            row["file"] = raw
        row.pop("file_ref", None)

    json_path = dest_dir / "documents_manifest.json"
    json_path.write_text(json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8")
    return csv_path, json_path


def export_audit_ledger(database_url: str, dest_dir: Path) -> Path:
    """Write the action audit-ledger (`mutation_records`) as JSON."""
    csv_text = _psql_copy_csv(database_url, AUDIT_SQL)
    rows = list(_csv_rows(csv_text))
    for row in rows:
        raw = row.get("args") or "{}"
        try:
            row["args"] = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            pass
    path = dest_dir / "audit_ledger.json"
    path.write_text(json.dumps(rows, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _csv_rows(csv_text: str) -> Iterable[dict[str, str]]:
    if not csv_text.strip():
        return []
    reader = csv.DictReader(io.StringIO(csv_text))
    return list(reader)


def write_export_readme(dest_dir: Path, target: ClientTarget, when: datetime) -> Path:
    text = f"""# brokerops offboarding export — {target.slug}

Generated: {when.isoformat()}
Project:   {target.project}
Posture:   {target.posture}

## Contents

| File | What |
|------|------|
| `database.dump` | Full Postgres custom-format dump (`pg_restore -d <db> database.dump`) |
| `documents_manifest.csv` / `.json` | `documents` table FileRefs — pointers into the client's Drive; no file bytes |
| `audit_ledger.json` | Action audit-ledger (`mutation_records`) — every external write on record |
| `MANIFEST.json` | This export's inventory + checksums |

## Retention stance

We hand over this archive. After a successful hosted (A1) destroy we keep **only** the
fleet-registry line (`offboarded_at` date) — nothing else from this client. See
`docs/OFFBOARDING.md`.
"""
    path = dest_dir / "README.md"
    path.write_text(text, encoding="utf-8")
    return path


def build_export_tree(
    target: ClientTarget,
    database_url: str,
    work_dir: Path,
    when: datetime | None = None,
) -> Path:
    """Populate work_dir with the export payload; return the directory."""
    when = when or datetime.now(timezone.utc)
    payload = work_dir / "payload"
    payload.mkdir(parents=True, exist_ok=True)

    dump_path = payload / "database.dump"
    print(f"  → pg_dump → {dump_path.name}  ({redact_database_url(database_url)})")
    run_pg_dump(database_url, dump_path)

    print("  → documents FileRef manifest (CSV + JSON)")
    export_documents_manifest(database_url, payload)

    print("  → audit ledger (mutation_records JSON)")
    export_audit_ledger(database_url, payload)

    write_export_readme(payload, target, when)

    inventory = {
        "slug": target.slug,
        "project": target.project,
        "posture": target.posture,
        "generated_at": when.isoformat(),
        "files": sorted(p.name for p in payload.iterdir() if p.is_file()),
    }
    (payload / "MANIFEST.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    return payload


def pack_archive(payload_dir: Path, archive_path: Path) -> Path:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in sorted(payload_dir.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(payload_dir).as_posix())
    return archive_path


# ── secret scan ──────────────────────────────────────────────────────────────


@dataclass
class ScanHit:
    path: str
    rule: str


# Longest pattern prefix we must keep across chunk boundaries (PEM header + AWS key id).
_SCAN_OVERLAP = 128
_SCAN_CHUNK = 1_048_576  # 1 MiB — stream full members without loading whole dump into RAM


def scan_bytes(label: str, data: bytes) -> list[ScanHit]:
    hits: list[ScanHit] = []
    for rule, pattern in _SECRET_PATTERNS:
        if pattern.search(data):
            hits.append(ScanHit(path=label, rule=rule))
    return hits


def scan_stream(label: str, stream: Any) -> list[ScanHit]:
    """Scan an entire readable binary stream in overlapping chunks (no size cap)."""
    found: set[str] = set()
    hits: list[ScanHit] = []
    carry = b""
    while True:
        chunk = stream.read(_SCAN_CHUNK)
        if not chunk:
            break
        window = carry + chunk
        for rule, pattern in _SECRET_PATTERNS:
            if rule in found:
                continue
            if pattern.search(window):
                found.add(rule)
                hits.append(ScanHit(path=label, rule=rule))
        carry = window[-_SCAN_OVERLAP:] if len(window) > _SCAN_OVERLAP else window
    return hits


def scan_archive(archive_path: Path) -> list[ScanHit]:
    """Scan every member of a .tar.gz in full (chunked) for secret shapes."""
    hits: list[ScanHit] = []
    with archive_path.open("rb") as fh:
        hits.extend(scan_stream(archive_path.name, fh))
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            hits.extend(scan_stream(member.name, extracted))
    return hits


def assert_archive_clean(archive_path: Path) -> None:
    hits = scan_archive(archive_path)
    if hits:
        detail = "\n".join(f"  - {h.path}: {h.rule}" for h in hits)
        raise OffboardError(
            f"secret scan FAILED on {archive_path} — refusing to deliver/destroy:\n{detail}\n"
            f"Inspect the export, redact, and re-run. See docs/OFFBOARDING.md."
        )
    print(f"  ✓ secret scan clean ({archive_path.name})")


# ── delivery ─────────────────────────────────────────────────────────────────


def deliver(archive_path: Path, dest: str) -> str:
    """Copy/upload archive to a local directory or gs:// URI. Returns the final location."""
    if dest.startswith("gs://"):
        if not shutil.which("gsutil") and not shutil.which("gcloud"):
            raise OffboardError("gs:// delivery needs gsutil or gcloud on PATH")
        # Ensure trailing structure: gs://bucket/prefix/filename
        dest_uri = dest.rstrip("/") + "/" + archive_path.name
        cmd = (
            ["gsutil", "cp", str(archive_path), dest_uri]
            if shutil.which("gsutil")
            else ["gcloud", "storage", "cp", str(archive_path), dest_uri]
        )
        try:
            subprocess.run(cmd, check=True, text=True)
        except subprocess.CalledProcessError as e:
            raise OffboardError(f"GCS upload failed (exit {e.returncode}): {' '.join(cmd)}") from e
        return dest_uri

    dest_path = Path(dest).expanduser()
    if dest_path.exists() and dest_path.is_dir():
        final = dest_path / archive_path.name
    else:
        # Treat as a file path when it ends with .tar.gz / .tgz, else as a directory.
        if dest_path.suffixes[-2:] == [".tar", ".gz"] or dest_path.suffix == ".tgz":
            final = dest_path
            final.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest_path.mkdir(parents=True, exist_ok=True)
            final = dest_path / archive_path.name
    shutil.copy2(archive_path, final)
    return str(final.resolve())


# ── terraform destroy + secrets verification ─────────────────────────────────


def tf_destroy(target: ClientTarget, bucket: str, assume_yes: bool) -> None:
    if not target.tfvars_rel:
        raise OffboardError(
            f"client {target.slug!r}: cannot destroy without tfvars — refused mark-only path"
        )
    fu.tf_init(bucket, target.slug)
    cmd = [
        "terraform",
        f"-chdir={INFRA_DIR}",
        "destroy",
        "-input=false",
        f"-var-file={target.tfvars_rel}",
    ]
    if assume_yes:
        cmd.append("-auto-approve")
    print(f"  → terraform destroy ({target.slug} / {target.project})")
    try:
        subprocess.run(cmd, cwd=fleet.REPO_ROOT, check=True, text=True)
    except subprocess.CalledProcessError as e:
        raise OffboardError(f"terraform destroy failed (exit {e.returncode})") from e


def list_client_secrets(project: str, slug: str) -> list[str]:
    """Secret Manager ids still present for this client prefix. Empty list = clean."""
    prefix = f"brokerops-{slug}-"
    if not shutil.which("gcloud"):
        raise OffboardError("gcloud not on PATH — cannot verify Secret Manager cleanup")
    try:
        result = subprocess.run(
            [
                "gcloud",
                "secrets",
                "list",
                f"--project={project}",
                "--format=value(name)",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise OffboardError(
            f"gcloud secrets list failed for project {project!r} (exit {e.returncode}) — "
            f"cannot verify secrets cleanup"
        ) from e
    names: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # format value(name) yields projects/.../secrets/SECRET_ID
        secret_id = line.rsplit("/", 1)[-1]
        if secret_id.startswith(prefix):
            names.append(secret_id)
    return sorted(names)


def verify_secrets_gone(project: str, slug: str) -> None:
    remaining = list_client_secrets(project, slug)
    if remaining:
        raise OffboardError(
            f"Secret Manager still has {len(remaining)} client secret(s) after destroy "
            f"in project {project!r}: {remaining}. Destroy them explicitly, then re-run "
            f"with --skip-export --mark-only if the infra is already gone."
        )
    print(f"  ✓ Secret Manager clean for prefix brokerops-{slug}- in {project}")


def secret_projects(target: ClientTarget, extra: list[str] | None) -> list[str]:
    """Projects to scan for client secrets: deploy project + any explicit extras.

    Spec: secrets are destroyed with the project *or* verified explicitly if they
    live outside it. Extras come from --secret-project and/or OFFBOARD_SECRET_PROJECTS.
    """
    projects: list[str] = [target.project]
    for p in extra or []:
        if p and p not in projects:
            projects.append(p)
    env = os.environ.get("OFFBOARD_SECRET_PROJECTS", "")
    for p in (x.strip() for x in env.split(",") if x.strip()):
        if p not in projects:
            projects.append(p)
    return projects


def verify_secrets_gone_all(projects: list[str], slug: str) -> None:
    for project in projects:
        verify_secrets_gone(project, slug)


def list_billable_hints(
    project: str, slug: str, scheduler_locations: list[str]
) -> dict[str, list[str]]:
    """GCP leftover check for billable classes this module creates. Failures raise."""
    if not shutil.which("gcloud"):
        raise OffboardError(
            "gcloud not on PATH — cannot verify post-destroy cleanup. "
            "Install gcloud or pass --skip-secrets-verify only for offline unit tests."
        )
    prefix = f"brokerops-{slug}"
    hints: dict[str, list[str]] = {}
    # Resource classes the brokerops module actually creates (main.tf / services /
    # database / secrets / scheduler). Probe failures are hard errors — never "clean".
    checks = [
        (
            "run_services",
            ["gcloud", "run", "services", "list", f"--project={project}", "--format=value(name)"],
        ),
        (
            "sql_instances",
            ["gcloud", "sql", "instances", "list", f"--project={project}", "--format=value(name)"],
        ),
        (
            "service_accounts",
            [
                "gcloud",
                "iam",
                "service-accounts",
                "list",
                f"--project={project}",
                "--format=value(email)",
            ],
        ),
    ]
    for key, cmd in checks:
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as e:
            raise OffboardError(
                f"post-destroy probe failed for {key} in project {project!r}: {e}. "
                f"Cannot confirm teardown left no billable resources."
            ) from e
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or "").strip() or str(e)
            raise OffboardError(
                f"post-destroy probe FAILED for {key} in project {project!r} "
                f"(exit {e.returncode}): {detail[:500]}. "
                f"Not treating as clean — fix gcloud access/format or re-run destroy."
            ) from e
        names = [n.strip() for n in result.stdout.splitlines() if n.strip() and prefix in n]
        hints[key] = names

    # Cloud Scheduler requires an explicit location; never `--location=-` (not portable).
    # Locations are derived from the client's Terraform config (scheduler_locations_for),
    # so we probe exactly where the job was provisioned — not an incomplete guess-list.
    # Probe failures are loud OffboardErrors — never false success, never a silent abort.
    hints["scheduler_jobs"] = _probe_scheduler_jobs(project, prefix, scheduler_locations)
    hints["secrets"] = list_client_secrets(project, slug)
    return hints


def _read_region(tfvars_abs: Path) -> str | None:
    """The `region = "..."` value in a client's tfvars, or None if absent/missing file."""
    if not tfvars_abs.exists():
        return None
    m = re.search(r'^\s*region\s*=\s*"([^"]*)"', tfvars_abs.read_text(), re.MULTILINE)
    return m.group(1) if m else None


def scheduler_locations_for(target: ClientTarget) -> list[str]:
    """Cloud Scheduler region(s) to probe — derived from the client's Terraform config.

    The module creates exactly one scheduler job, in `var.region` (modules/brokerops/
    scheduler.tf). So the authoritative probe location is that client's configured region:
    the `region` in its tfvars, or the Terraform-declared default when the tfvars omits it.
    OFFBOARD_SCHEDULER_LOCATION overrides for the rare case the job was relocated. If the
    region cannot be determined (no tfvars, no override), fail closed rather than guess an
    incomplete hard-coded list.
    """
    override = (os.environ.get("OFFBOARD_SCHEDULER_LOCATION") or "").strip()
    if override:
        if override == "-":
            raise OffboardError(
                "OFFBOARD_SCHEDULER_LOCATION='-' is not supported (gcloud requires an "
                "explicit region). Set a real location e.g. us-west1."
            )
        return [override]
    if target.tfvars_rel:
        # tfvars present → its `region` (or Terraform's default when it omits region) is
        # exactly where the scheduler job was provisioned.
        return [_read_region(INFRA_DIR / target.tfvars_rel) or TERRAFORM_DEFAULT_REGION]
    raise OffboardError(
        f"client {target.slug!r}: cannot determine the Cloud Scheduler region from Terraform "
        f"config (no tfvars available). Set OFFBOARD_SCHEDULER_LOCATION to the deploy region "
        f"so cleanup verification can prove no scheduler jobs remain — never assume clean."
    )


def _probe_scheduler_jobs(project: str, prefix: str, locations: list[str]) -> list[str]:
    """List brokerops-prefixed Cloud Scheduler jobs across the given explicit locations."""
    sched_names: list[str] = []
    for loc in locations:
        cmd = [
            "gcloud",
            "scheduler",
            "jobs",
            "list",
            f"--project={project}",
            f"--location={loc}",
            "--format=value(name)",
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as e:
            raise OffboardError(
                f"post-destroy probe failed for scheduler_jobs@{loc} in project {project!r}: "
                f"{e}. Cannot confirm teardown left no billable resources."
            ) from e
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or e.stdout or "").strip() or str(e)
            raise OffboardError(
                f"post-destroy scheduler probe FAILED at location={loc!r} in project "
                f"{project!r} (exit {e.returncode}): {detail[:500]}. "
                f"Not treating as clean. Pin the region with OFFBOARD_SCHEDULER_LOCATION "
                f"if jobs live elsewhere, or fix gcloud credentials/API enablement."
            ) from e
        for n in result.stdout.splitlines():
            n = n.strip()
            if n and prefix in n and n not in sched_names:
                sched_names.append(n)
    return sched_names


def verify_tf_state_empty(bucket: str, slug: str) -> None:
    """Authoritative: after destroy, terraform state for this client must list nothing."""
    fu.tf_init(bucket, slug)
    try:
        result = subprocess.run(
            ["terraform", f"-chdir={INFRA_DIR}", "state", "list"],
            cwd=fleet.REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise OffboardError(
            f"terraform state list failed for {slug!r} — cannot prove destroy emptied state: {e}"
        ) from e
    resources = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if resources:
        preview = ", ".join(resources[:12])
        more = f" (+{len(resources) - 12} more)" if len(resources) > 12 else ""
        raise OffboardError(
            f"terraform state for {slug!r} is not empty after destroy ({len(resources)} resource(s)): "
            f"{preview}{more}. Re-run destroy until state is empty."
        )
    print(f"  ✓ terraform state empty for brokerops/{slug}")


# ── registry mark ────────────────────────────────────────────────────────────


_ENTRY_RE = re.compile(r"^(?P<indent>\s*)-\s+slug:\s*(?P<slug>[^\s#]+)")
_OFFBOARDED_RE = re.compile(r"^(?P<i>\s*)offboarded_at:\s*(?P<v>\S+)(?P<rest>.*)$")
_UPGRADED_RE = re.compile(r"^(?P<i>\s*)last_upgraded:\s*(?P<v>\S+)(?P<rest>.*)$")


def mark_offboarded(text: str, slug: str, when: date) -> str:
    """Return manifest text with `slug`'s `offboarded_at` set (insert or replace).

    Comment-preserving surgical edit — same discipline as fleet_upgrade.update_manifest_entry.
    """
    lines = text.splitlines(keepends=True)
    start = _entry_start(lines, slug)
    if start is None:
        raise OffboardError(
            f"{fleet.MANIFEST.name}: no entry with slug {slug!r} to mark offboarded"
        )
    end = _entry_end(lines, start)

    iso = when.isoformat()
    for i in range(start, end):
        body, nl = _split_newline(lines[i])
        m = _OFFBOARDED_RE.match(body)
        if m:
            lines[i] = f"{m['i']}offboarded_at: {iso}{m['rest']}{nl}"
            return "".join(lines)

    # Insert immediately after last_upgraded when present; else after the slug line.
    insert_at = start + 1
    indent = "    "
    for i in range(start, end):
        body, _nl = _split_newline(lines[i])
        um = _UPGRADED_RE.match(body)
        if um:
            insert_at = i + 1
            indent = um["i"]
            break
        sm = _ENTRY_RE.match(body)
        if sm and sm["slug"] == slug:
            indent = sm["indent"] + "  "

    lines.insert(insert_at, f"{indent}offboarded_at: {iso}\n")
    return "".join(lines)


def write_manifest_offboarded(slug: str, when: date) -> None:
    text = fleet.MANIFEST.read_text()
    new_text = mark_offboarded(text, slug, when)
    fleet.load_manifest(text=new_text)  # fail loud before writing
    fleet.MANIFEST.write_text(new_text)


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


# ── confirm ──────────────────────────────────────────────────────────────────


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="offboard_client.py",
        description="Export client data, destroy the instance (confirm-gated), mark registry offboarded.",
    )
    p.add_argument("slug", help="fleet registry slug to offboard")
    p.add_argument(
        "--dest",
        required=True,
        help="local directory/file or gs://bucket/prefix for the export archive",
    )
    p.add_argument(
        "--database-url",
        default=None,
        help="Postgres DSN (else CLIENT_DATABASE_URL / DATABASE_URL)",
    )
    p.add_argument(
        "--mode",
        choices=("hosted", "client-infra", "auto"),
        default="auto",
        help="hosted = A1 destroy; client-infra = B-tier (no destroy); auto = use registry posture",
    )
    p.add_argument("--yes", action="store_true", help="skip confirm prompts (non-interactive)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan only — no export write, no destroy, no registry edit",
    )
    p.add_argument(
        "--export-only",
        action="store_true",
        help="build + deliver + scan the archive; skip destroy and registry mark",
    )
    p.add_argument(
        "--skip-export",
        action="store_true",
        help="skip export (archive already delivered); still destroy/mark as requested",
    )
    p.add_argument(
        "--skip-destroy",
        action="store_true",
        help="skip terraform destroy (e.g. already destroyed; still verify secrets + mark)",
    )
    p.add_argument(
        "--mark-only",
        action="store_true",
        help="only set offboarded_at in the registry (no export, no destroy)",
    )
    p.add_argument(
        "--skip-secrets-verify",
        action="store_true",
        help="do not call gcloud secrets list after destroy (offline/tests only)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="allow re-running offboard steps on a slug already marked offboarded_at "
        "(default: refuse — already-complete offboards are terminal)",
    )
    p.add_argument(
        "--secret-project",
        action="append",
        default=[],
        metavar="PROJECT_ID",
        help="extra GCP project to scan for brokerops-<slug>-* secrets (repeatable). "
        "Also honors OFFBOARD_SECRET_PROJECTS=proj1,proj2. Deploy project is always scanned.",
    )
    args = p.parse_args(argv)
    if args.export_only and args.skip_export:
        p.error("--export-only and --skip-export are mutually exclusive")
    if args.mark_only and (args.export_only or args.skip_export):
        p.error("--mark-only cannot be combined with --export-only / --skip-export")
    return args


def _effective_mode(args_mode: str, posture: str) -> str:
    if args_mode == "auto":
        return posture
    return args_mode


def plan_lines(target: ClientTarget, mode: str, args: argparse.Namespace) -> list[str]:
    lines = [
        f"client:     {target.slug}",
        f"project:    {target.project}",
        f"posture:    {target.posture} (mode={mode})",
        f"tfvars:     {target.tfvars_rel}",
        f"dest:       {args.dest}",
    ]
    if target.already_offboarded:
        lines.append(
            f"note:       already marked offboarded_at={target.already_offboarded.isoformat()}"
        )
    if args.mark_only:
        lines.append("steps:      mark registry offboarded_at only")
        return lines
    steps: list[str] = []
    if not args.skip_export:
        steps.append("export (pg_dump + documents manifest + audit ledger)")
        steps.append("deliver archive + secret scan")
    if mode == "client-infra":
        steps.append("B-tier: skip terraform destroy (client keeps project)")
        steps.append("B-tier: operator must revoke WebDrvn access (see runbook)")
    elif args.export_only:
        steps.append("skip terraform destroy")
    elif args.skip_destroy:
        steps.append("skip terraform destroy")
        if not args.skip_secrets_verify:
            steps.append("verify cleanup still (secrets + Cloud Run + SQL empty)")
    else:
        steps.append("confirm-gated terraform destroy")
        if not args.skip_secrets_verify:
            steps.append("verify cleanup (secrets + Cloud Run + SQL empty)")
    if not args.export_only:
        steps.append("mark fleet.yml offboarded_at (history kept)")
    for i, s in enumerate(steps, 1):
        lines.append(f"  {i}. {s}")
    return lines


def _real_leftovers(hints: dict[str, list[str]]) -> dict[str, list[str]]:
    """Non-empty leftover name lists from a successful list_billable_hints call."""
    return {key: values for key, values in hints.items() if values}


def verify_hosted_cleanup(
    project: str,
    slug: str,
    *,
    secret_project_list: list[str],
    tf_state_bucket: str | None,
    scheduler_locations: list[str],
) -> None:
    """Prove A1 teardown: secrets (all known projects) + GCP leftovers + empty TF state."""
    verify_secrets_gone_all(secret_project_list, slug)
    leftovers = _real_leftovers(list_billable_hints(project, slug, scheduler_locations))
    if leftovers:
        raise OffboardError(
            f"brokerops resources still present in project {project!r}: {leftovers}. "
            f"Clean them up before recording offboarded_at."
        )
    print("  ✓ no brokerops-prefixed Cloud Run / SQL / scheduler / SA / secrets found")
    if tf_state_bucket:
        verify_tf_state_empty(tf_state_bucket, slug)
    else:
        # skip-destroy recovery without a bucket: GCP probes still ran; state check needs
        # TF_STATE_BUCKET. Refuse to claim full proof without it.
        raise OffboardError(
            "set TF_STATE_BUCKET to prove terraform state is empty after teardown "
            "(authoritative 'no billable resources we manage' check). "
            "Offline tests may pass --skip-secrets-verify."
        )


def run(args: argparse.Namespace) -> int:
    # mark-only must work after teardown when tfvars are already gone.
    require_tfvars = not args.mark_only

    try:
        fleet_obj = fleet.load_manifest()
        overlay = fleet.load_overlay()
        target = resolve_client(fleet_obj, overlay, args.slug, require_tfvars=require_tfvars)
    except (fleet.FleetError, OffboardError) as e:
        print(str(e), file=sys.stderr)
        return 1

    mode = _effective_mode(args.mode, target.posture)
    print("── offboard plan ──────────────────────────────")
    for line in plan_lines(target, mode, args):
        print(line)

    if args.dry_run:
        print("\n(dry run: no export, no destroy, no registry edit)")
        return 0

    # Already-complete offboards are terminal unless --force (idempotent refuse).
    if target.already_offboarded is not None and not args.force:
        print(
            f"{target.slug} is already offboarded (offboarded_at="
            f"{target.already_offboarded.isoformat()}). Refusing to re-run destroy/export/mark. "
            f"Pass --force only if you intentionally need a re-export or recovery step.",
            file=sys.stderr,
        )
        return 1

    when = date.today()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"brokerops-offboard-{target.slug}-{stamp}.tar.gz"

    try:
        if args.mark_only:
            if not args.yes and not _confirm(
                f"mark {target.slug} offboarded_at={when.isoformat()}?"
            ):
                raise OffboardError("mark declined at the confirm prompt")
            write_manifest_offboarded(target.slug, when)
            print(f"✓ {target.slug} marked offboarded_at={when.isoformat()} in {fleet.MANIFEST}")
            return 0

        delivered: str | None = None
        if not args.skip_export:
            database_url = resolve_database_url(args.database_url)
            with tempfile.TemporaryDirectory(prefix=f"offboard-{target.slug}-") as tmp:
                tmp_path = Path(tmp)
                payload = build_export_tree(target, database_url, tmp_path)
                archive_path = tmp_path / archive_name
                pack_archive(payload, archive_path)
                assert_archive_clean(archive_path)
                delivered = deliver(archive_path, args.dest)
            print(f"  ✓ archive delivered → {delivered}")

        if args.export_only:
            print("✓ export-only complete (destroy + registry mark skipped)")
            return 0

        if mode == "client-infra":
            print(
                "  · B-tier: terraform destroy skipped — client keeps project + data.\n"
                "    Revoke WebDrvn operator access (IAM / SA keys / state bucket ACLs) "
                "per docs/OFFBOARDING.md §B-tier, then continue to registry mark."
            )
        elif not args.skip_destroy:
            bucket = os.environ.get("TF_STATE_BUCKET")
            if not bucket:
                raise OffboardError(
                    "set TF_STATE_BUCKET to run terraform destroy (or pass --skip-destroy / "
                    "--export-only / --mode client-infra)"
                )
            if not target.tfvars_rel:
                raise OffboardError(
                    f"client {target.slug!r}: tfvars missing — cannot destroy; use --mark-only "
                    f"if infra is already gone, or restore the tfvars file"
                )
            prompt = (
                f"DESTROY all brokerops infra for {target.slug} in project {target.project}? "
                f"This is irreversible."
            )
            if not args.yes and not _confirm(prompt):
                raise OffboardError("destroy declined at the confirm prompt")
            # Human gate is our prompt; terraform gets -auto-approve once past it.
            tf_destroy(target, bucket, assume_yes=True)
            if not args.skip_secrets_verify:
                verify_hosted_cleanup(
                    target.project,
                    target.slug,
                    secret_project_list=secret_projects(target, args.secret_project),
                    tf_state_bucket=bucket,
                    scheduler_locations=scheduler_locations_for(target),
                )
        elif mode != "client-infra" and not args.skip_secrets_verify:
            # Hosted + --skip-destroy (infra already gone): still prove secrets/resources clean
            # before recording offboarded_at — cleanup is never assumed.
            print("  → verifying cleanup (skip-destroy: no terraform, still prove clean)")
            bucket = os.environ.get("TF_STATE_BUCKET")
            verify_hosted_cleanup(
                target.project,
                target.slug,
                secret_project_list=secret_projects(target, args.secret_project),
                tf_state_bucket=bucket,
                scheduler_locations=scheduler_locations_for(target),
            )

        if not args.yes and not _confirm(
            f"mark {target.slug} offboarded_at={when.isoformat()} in fleet.yml?"
        ):
            raise OffboardError(
                "registry mark declined — export/destroy may already be done; "
                "re-run with --mark-only to record offboarded_at"
            )
        write_manifest_offboarded(target.slug, when)
        print(f"✓ {target.slug} offboarded_at={when.isoformat()} recorded in {fleet.MANIFEST.name}")
        if delivered:
            print(f"  archive: {delivered}")
        return 0

    except OffboardError as e:
        print(str(e), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"command failed (exit {e.returncode})", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
