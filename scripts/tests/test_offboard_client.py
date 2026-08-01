"""Offboarding path tests (BOP-036).

Pure logic: registry mark, secret scan, resolve, plan, archive pack/scan, redaction.
Local export drill: when Docker is available, seed a throwaway Postgres with documents
+ mutation_records, run pg_dump + manifests, prove the archive is restorable-shaped and
secret-clean. Terraform destroy / live GCP are manual (docs/OFFBOARDING.md) — same split
as BOP-033.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import textwrap
from datetime import date
from pathlib import Path

import pytest

import fleet
import offboard_client as oc


MANIFEST_TEXT = """\
# header must survive
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


def _tfvars(tmp_path: Path, name: str, project: str = "brokerops-x") -> Path:
    d = tmp_path / "clients"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.tfvars"
    p.write_text(f'client_name = "{name}"\nproject_id  = "{project}"\nimage_version = "latest"\n')
    return p


# ── resolve ──────────────────────────────────────────────────────────────────


def test_resolve_client_from_tfvars(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "brokerops-demo")
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)
    t = oc.resolve_client(fleet_obj, {}, "demo")
    assert t.slug == "demo" and t.project == "brokerops-demo" and t.posture == "hosted"
    assert t.already_offboarded is None


def test_resolve_unknown_slug_raises(fleet_obj: fleet.Fleet) -> None:
    with pytest.raises(oc.OffboardError, match="not in"):
        oc.resolve_client(fleet_obj, {}, "ghost")


def test_resolve_missing_tfvars_raises(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)
    with pytest.raises(oc.OffboardError, match="no tfvars"):
        oc.resolve_client(fleet_obj, {}, "demo")


# ── registry mark ────────────────────────────────────────────────────────────


def test_mark_offboarded_inserts_after_last_upgraded() -> None:
    out = oc.mark_offboarded(MANIFEST_TEXT, "acme", date(2026, 7, 31))
    assert "offboarded_at: 2026-07-31" in out
    # header + other entry untouched
    assert out.startswith("# header must survive")
    assert "slug: demo" in out
    fleet_obj = fleet.load_manifest(text=out)
    acme = next(c for c in fleet_obj.clients if c.slug == "acme")
    assert acme.offboarded_at == date(2026, 7, 31)
    assert acme.is_offboarded is True
    demo = next(c for c in fleet_obj.clients if c.slug == "demo")
    assert demo.offboarded_at is None


def test_mark_offboarded_replaces_existing() -> None:
    base = oc.mark_offboarded(MANIFEST_TEXT, "demo", date(2026, 1, 1))
    out = oc.mark_offboarded(base, "demo", date(2026, 7, 31))
    assert out.count("offboarded_at:") == 1
    demo = next(c for c in fleet.load_manifest(text=out).clients if c.slug == "demo")
    assert demo.offboarded_at == date(2026, 7, 31)


def test_mark_offboarded_unknown_slug_raises() -> None:
    with pytest.raises(oc.OffboardError, match="no entry"):
        oc.mark_offboarded(MANIFEST_TEXT, "ghost", date(2026, 7, 31))


def test_fleet_render_shows_offboarded_status() -> None:
    text = oc.mark_offboarded(MANIFEST_TEXT, "acme", date(2026, 7, 15))
    fleet_obj = fleet.load_manifest(text=text)
    out = fleet.render(fleet_obj, overlay={}, latest="v0.1.0")
    assert "STATUS" in out
    assert "offboarded 2026-07-15" in out
    assert "active" in out  # demo still active


def test_fleet_upgrade_skips_offboarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import fleet_upgrade as fu

    text = oc.mark_offboarded(MANIFEST_TEXT, "acme", date(2026, 7, 15))
    fleet_obj = fleet.load_manifest(text=text)
    _tfvars(tmp_path, "demo", "brokerops-demo")
    _tfvars(tmp_path, "acme", "brokerops-acme")
    monkeypatch.setattr(fu, "INFRA_DIR", tmp_path)

    targets = fu.resolve_targets(fleet_obj, {}, "v0.2.0", None)
    assert [t.slug for t in targets] == ["demo"]  # acme skipped
    err = capsys.readouterr().err
    assert "offboarded" in err and "acme" in err


# ── secret scan ──────────────────────────────────────────────────────────────


def test_scan_detects_postgres_url_with_password() -> None:
    hits = oc.scan_bytes("x", b"dsn=postgresql://brokerops:s3cretpass@localhost:5432/db")
    assert any(h.rule == "postgres-url-with-password" for h in hits)


def test_scan_detects_pem_private_key() -> None:
    hits = oc.scan_bytes("k", b"-----BEGIN RSA PRIVATE KEY-----\nMIIE\n")
    assert any(h.rule == "pem-private-key" for h in hits)


def test_scan_detects_aws_key() -> None:
    # AWS access key ids are AKIA + 16 alphanumeric chars.
    hits = oc.scan_bytes("a", b"key=AKIAIOSFODNN7EXAMPLE")
    assert any(h.rule == "aws-access-key-id" for h in hits)


def test_scan_clean_documents_manifest() -> None:
    payload = json.dumps(
        [
            {
                "id": "d1",
                "file": {
                    "file_id": "abc",
                    "name": "purchase.pdf",
                    "mime_type": "application/pdf",
                    "web_url": "https://drive.google.com/file/d/abc",
                },
            }
        ]
    ).encode()
    assert oc.scan_bytes("documents_manifest.json", payload) == []


def test_assert_archive_clean_passes_and_fails(tmp_path: Path) -> None:
    clean = tmp_path / "clean.tar.gz"
    with tarfile.open(clean, "w:gz") as tar:
        f = tmp_path / "documents_manifest.json"
        f.write_text('[{"id":"d1","file":{"file_id":"x","name":"a.pdf"}}]\n')
        tar.add(f, arcname="documents_manifest.json")
    oc.assert_archive_clean(clean)

    dirty = tmp_path / "dirty.tar.gz"
    with tarfile.open(dirty, "w:gz") as tar:
        f = tmp_path / "leak.txt"
        f.write_bytes(b"url=postgresql://u:p@host/db\n")
        tar.add(f, arcname="leak.txt")
    with pytest.raises(oc.OffboardError, match="secret scan FAILED"):
        oc.assert_archive_clean(dirty)


def test_scan_stream_detects_secret_past_first_chunk() -> None:
    """Gate r1: scan must not stop at a fixed window — secret deep in the stream still hits."""
    import io

    # Place a DSN well past one full chunk so a head-only scan would miss it.
    secret = b"postgresql://brokerops:deepsecret@db.example/brokerops_demo"
    payload = (b"x" * (oc._SCAN_CHUNK + 100)) + secret + b"y" * 50
    hits = oc.scan_stream("deep.bin", io.BytesIO(payload))
    assert any(h.rule == "postgres-url-with-password" for h in hits)


def test_resolve_mark_only_without_tfvars(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)
    t = oc.resolve_client(fleet_obj, {}, "demo", require_tfvars=False)
    assert t.tfvars_rel is None
    assert t.slug == "demo"


def test_already_offboarded_refuses_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    text = oc.mark_offboarded(MANIFEST_TEXT, "demo", date(2026, 7, 1))
    manifest_path = tmp_path / "fleet.yml"
    manifest_path.write_text(text)
    monkeypatch.setattr(fleet, "MANIFEST", manifest_path)
    _tfvars(tmp_path, "demo", "brokerops-demo")
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(fleet, "load_overlay", lambda text=None: {})
    rc = oc.main(["demo", "--dest", str(tmp_path / "out"), "--export-only", "--yes"])
    assert rc == 1
    assert "already offboarded" in capsys.readouterr().err


# ── redaction / plan / CLI dry-run ───────────────────────────────────────────


def test_secret_projects_includes_extras_and_env(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "brokerops-demo")
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)
    t = oc.resolve_client(fleet_obj, {}, "demo")
    monkeypatch.setenv("OFFBOARD_SECRET_PROJECTS", "shared-secrets, other-proj")
    assert oc.secret_projects(t, ["extra-1"]) == [
        "brokerops-demo",
        "extra-1",
        "shared-secrets",
        "other-proj",
    ]


def test_verify_tf_state_empty_fails_on_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc.fu, "tf_init", lambda bucket, slug: None)

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class R:
            stdout = "module.client.google_cloud_run_v2_service.api\n"
            returncode = 0

        return R()

    monkeypatch.setattr(oc.subprocess, "run", fake_run)
    with pytest.raises(oc.OffboardError, match="state for .* is not empty"):
        oc.verify_tf_state_empty("bucket", "demo")


def test_verify_tf_state_empty_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc.fu, "tf_init", lambda bucket, slug: None)

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        class R:
            stdout = ""
            returncode = 0

        return R()

    monkeypatch.setattr(oc.subprocess, "run", fake_run)
    oc.verify_tf_state_empty("bucket", "demo")  # no raise


def test_redact_database_url_strips_password() -> None:
    out = oc.redact_database_url("postgresql://brokerops:hunter2@localhost:5432/brokerops_demo")
    assert "hunter2" not in out
    assert "brokerops:***@localhost" in out


def test_strip_dsn_password_removes_secret() -> None:
    # Password removed entirely (auth moves to PGPASSWORD); user/host/port/db preserved.
    assert (
        oc.strip_dsn_password("postgresql://brokerops:hunter2@localhost:5432/brokerops_demo")
        == "postgresql://brokerops@localhost:5432/brokerops_demo"
    )
    # No password → unchanged.
    assert (
        oc.strip_dsn_password("postgresql://brokerops@localhost/db")
        == "postgresql://brokerops@localhost/db"
    )


def test_run_pg_dump_never_puts_password_in_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate r5 BLOCKER: pg_dump argv must not carry the DSN password (process-listing leak)."""
    monkeypatch.setattr(
        oc.shutil, "which", lambda b: "/usr/bin/pg_dump" if b == "pg_dump" else None
    )
    captured: dict[str, object] = {}
    out_file = tmp_path / "database.dump"

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        out_file.write_bytes(b"PGDMP fake dump")  # satisfy the non-empty check

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(oc.subprocess, "run", fake_run)
    oc.run_pg_dump("postgresql://brokerops:hunter2@localhost:5432/brokerops_demo", out_file)

    argv = " ".join(str(a) for a in captured["cmd"])  # type: ignore[arg-type]
    assert "hunter2" not in argv
    assert captured["env"]["PGPASSWORD"] == "hunter2"  # type: ignore[index]


def test_psql_copy_csv_never_puts_password_in_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate r5 BLOCKER: psql argv must not carry the DSN password."""
    monkeypatch.setattr(oc.shutil, "which", lambda b: "/usr/bin/psql" if b == "psql" else None)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")

        class R:
            stdout = "id\n1\n"
            returncode = 0

        return R()

    monkeypatch.setattr(oc.subprocess, "run", fake_run)
    oc._psql_copy_csv("postgresql://brokerops:hunter2@localhost:5432/brokerops_demo", "SELECT 1")

    argv = " ".join(str(a) for a in captured["cmd"])  # type: ignore[arg-type]
    assert "hunter2" not in argv
    assert captured["env"]["PGPASSWORD"] == "hunter2"  # type: ignore[index]


def test_plan_lines_hosted_vs_client_infra(
    fleet_obj: fleet.Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tfvars(tmp_path, "demo", "brokerops-demo")
    _tfvars(tmp_path, "acme", "brokerops-acme")
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)

    demo = oc.resolve_client(fleet_obj, {}, "demo")
    args = oc.parse_args(["demo", "--dest", "./exports"])
    plan = "\n".join(oc.plan_lines(demo, "hosted", args))
    assert "terraform destroy" in plan

    acme = oc.resolve_client(fleet_obj, {}, "acme")
    plan_b = "\n".join(oc.plan_lines(acme, "client-infra", args))
    assert "skip terraform destroy" in plan_b
    assert "Revoke" in plan_b or "revoke" in plan_b.lower() or "B-tier" in plan_b


def test_main_dry_run_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Real committed registry + real demo.tfvars — dry-run must not write.
    monkeypatch.chdir(fleet.REPO_ROOT)
    before = fleet.MANIFEST.read_text()
    rc = oc.main(["demo", "--dest", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert fleet.MANIFEST.read_text() == before
    out = capsys.readouterr().out
    assert "dry run" in out and "demo" in out


def test_main_rejects_unknown_slug(capsys: pytest.CaptureFixture[str]) -> None:
    assert oc.main(["no-such-client", "--dest", "/tmp/x", "--dry-run"]) == 1
    assert "not in" in capsys.readouterr().err


def test_export_only_and_skip_export_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        oc.parse_args(["demo", "--dest", "/tmp/x", "--export-only", "--skip-export"])


def _target(slug: str, tfvars_rel: str | None) -> oc.ClientTarget:
    return oc.ClientTarget(
        slug=slug,
        posture="hosted",
        project=f"brokerops-{slug}",
        tfvars_rel=tfvars_rel,
        already_offboarded=None,
    )


def test_scheduler_locations_from_tfvars_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OFFBOARD_SCHEDULER_LOCATION", raising=False)
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    # tfvars pins region → probe exactly that region (authoritative, complete).
    d = tmp_path / "clients"
    d.mkdir()
    (d / "demo.tfvars").write_text(
        'client_name = "demo"\nproject_id  = "brokerops-demo"\nregion = "europe-west1"\n'
    )
    assert oc.scheduler_locations_for(_target("demo", "clients/demo.tfvars")) == ["europe-west1"]


def test_scheduler_locations_defaults_to_terraform_default_when_region_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OFFBOARD_SCHEDULER_LOCATION", raising=False)
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    d = tmp_path / "clients"
    d.mkdir()
    # No `region` line → Terraform applies its declared default; probe follows it.
    (d / "demo.tfvars").write_text('client_name = "demo"\nproject_id  = "brokerops-demo"\n')
    assert oc.scheduler_locations_for(_target("demo", "clients/demo.tfvars")) == [
        oc.TERRAFORM_DEFAULT_REGION
    ]


def test_scheduler_locations_env_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    d = tmp_path / "clients"
    d.mkdir()
    (d / "demo.tfvars").write_text('region = "us-west1"\n')
    monkeypatch.setenv("OFFBOARD_SCHEDULER_LOCATION", "asia-east1")
    assert oc.scheduler_locations_for(_target("demo", "clients/demo.tfvars")) == ["asia-east1"]
    monkeypatch.setenv("OFFBOARD_SCHEDULER_LOCATION", "-")
    with pytest.raises(oc.OffboardError, match="not supported"):
        oc.scheduler_locations_for(_target("demo", "clients/demo.tfvars"))


def test_scheduler_locations_fail_closed_without_tfvars_or_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OFFBOARD_SCHEDULER_LOCATION", raising=False)
    with pytest.raises(oc.OffboardError, match="cannot determine the Cloud Scheduler region"):
        oc.scheduler_locations_for(_target("demo", None))


def test_probe_scheduler_jobs_loud_failure_on_gcloud_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(cmd, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.CalledProcessError(
            2, cmd, output="", stderr="ERROR: (gcloud.scheduler.jobs.list) INVALID_ARGUMENT"
        )

    monkeypatch.setattr(oc.subprocess, "run", boom)
    with pytest.raises(oc.OffboardError, match="scheduler probe FAILED"):
        oc._probe_scheduler_jobs("brokerops-demo", "brokerops-demo", ["us-west1"])


def test_pack_archive_roundtrip(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "README.md").write_text("hi\n")
    (payload / "documents_manifest.json").write_text("[]\n")
    archive = tmp_path / "out.tar.gz"
    oc.pack_archive(payload, archive)
    with tarfile.open(archive, "r:gz") as tar:
        names = sorted(tar.getnames())
    assert names == ["README.md", "documents_manifest.json"]


# ── local Postgres export drill ──────────────────────────────────────────────


def _docker_available() -> bool:
    return (
        shutil.which("docker") is not None
        and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    )


@pytest.fixture(scope="module")
def pg_dsn(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Throwaway Postgres 16 with documents + mutation_records seeded."""
    if not _docker_available():
        pytest.skip("docker not available for local offboard export drill")

    name = "bop036-offboard-pg"
    # Clean any prior run
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_USER=brokerops",
            "-e",
            "POSTGRES_PASSWORD=brokerops",
            "-e",
            "POSTGRES_DB=brokerops_demo",
            "-p",
            "55432:5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dsn = "postgresql://brokerops:brokerops@127.0.0.1:55432/brokerops_demo"

    # Wait until the *database* accepts queries (pg_isready can pass before initdb
    # finishes creating POSTGRES_DB — that race produced FATAL database does not exist).
    import time

    for _ in range(60):
        ready = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "-U",
                "brokerops",
                "-d",
                "brokerops_demo",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SELECT 1",
            ],
            capture_output=True,
        )
        if ready.returncode == 0:
            break
        time.sleep(0.5)
    else:
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True).stdout[
            -2000:
        ]
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        pytest.fail(f"postgres container never became ready\n{logs}")

    schema = textwrap.dedent(
        """
        CREATE TABLE documents (
          id text PRIMARY KEY,
          tenant_id text NOT NULL DEFAULT '',
          transaction_id text NOT NULL,
          milestone_id text,
          kind text NOT NULL DEFAULT 'other',
          title text NOT NULL DEFAULT '',
          file jsonb NOT NULL,
          uploaded_by text NOT NULL DEFAULT '',
          created_at timestamptz DEFAULT now()
        );
        CREATE TABLE mutation_records (
          id text PRIMARY KEY,
          tenant_id text NOT NULL DEFAULT '',
          workflow_run_id text NOT NULL DEFAULT '',
          workflow text NOT NULL DEFAULT '',
          transaction_id text NOT NULL DEFAULT '',
          tool text NOT NULL,
          integration text NOT NULL,
          args jsonb NOT NULL,
          approval_id text,
          actor text,
          outcome text NOT NULL,
          external_id text,
          error text,
          created_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO documents (id, tenant_id, transaction_id, kind, title, file, uploaded_by)
        VALUES (
          'doc-1', 'tenant-a', 'txn-1', 'purchase_agreement', 'PA',
          '{"file_id":"file-1","name":"pa.pdf","mime_type":"application/pdf","size_bytes":12,"web_url":"https://drive.example/file-1"}'::jsonb,
          'op@example.com'
        );
        INSERT INTO mutation_records (id, tenant_id, workflow_run_id, workflow, transaction_id,
                                      tool, integration, args, outcome)
        VALUES (
          'mut-1', 'tenant-a', 'run-1', 'transaction_coordination', 'txn-1',
          'crm.update', 'followupboss', '{"contact_id":"c1","note":"hello"}'::jsonb, 'success'
        );
        """
    )
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            name,
            "psql",
            "-U",
            "brokerops",
            "-d",
            "brokerops_demo",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=schema,
        check=True,
        text=True,
        capture_output=True,
    )

    yield dsn

    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_local_export_drill_produces_clean_restorable_archive(
    pg_dsn: str,
    tmp_path: Path,
    fleet_obj: fleet.Fleet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tfvars(tmp_path, "demo", "brokerops-demo")
    monkeypatch.setattr(oc, "INFRA_DIR", tmp_path)
    monkeypatch.setattr(oc.fu, "INFRA_DIR", tmp_path)
    target = oc.resolve_client(fleet_obj, {}, "demo")

    work = tmp_path / "work"
    work.mkdir()
    payload = oc.build_export_tree(target, pg_dsn, work)

    assert (payload / "database.dump").stat().st_size > 0
    docs = json.loads((payload / "documents_manifest.json").read_text())
    assert docs[0]["id"] == "doc-1"
    assert docs[0]["file"]["file_id"] == "file-1"
    assert "pa.pdf" in (payload / "documents_manifest.csv").read_text()
    audit = json.loads((payload / "audit_ledger.json").read_text())
    assert audit[0]["tool"] == "crm.update"
    assert (payload / "MANIFEST.json").exists()

    archive = tmp_path / "brokerops-offboard-demo-test.tar.gz"
    oc.pack_archive(payload, archive)
    oc.assert_archive_clean(archive)

    # Restorable: pg_restore -l lists the dump TOC (no target DB needed).
    list_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{archive.parent}:/out",
        "postgres:16-alpine",
        "bash",
        "-c",
        # extract dump from tar then list
        "cd /tmp && tar -xzf /out/brokerops-offboard-demo-test.tar.gz database.dump "
        "&& pg_restore -l database.dump | grep -E 'TABLE.*documents|TABLE.*mutation_records'",
    ]
    listed = subprocess.run(list_cmd, check=True, capture_output=True, text=True)
    assert "documents" in listed.stdout
    assert "mutation_records" in listed.stdout

    delivered = oc.deliver(archive, str(tmp_path / "handoff"))
    assert Path(delivered).is_file()
    assert Path(delivered).stat().st_size == archive.stat().st_size


def test_export_only_cli_against_local_pg(
    pg_dsn: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end CLI: export-only against local DSN + real demo registry entry."""
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setenv("CLIENT_DATABASE_URL", pg_dsn)
    # Point INFRA at real repo infra so demo.tfvars resolves.
    monkeypatch.chdir(fleet.REPO_ROOT)
    rc = oc.main(["demo", "--dest", str(dest), "--export-only", "--yes"])
    assert rc == 0
    archives = list(dest.glob("brokerops-offboard-demo-*.tar.gz"))
    assert len(archives) == 1
    oc.assert_archive_clean(archives[0])
    # Registry must NOT be marked on export-only.
    demo = next(c for c in fleet.load_manifest().clients if c.slug == "demo")
    assert demo.offboarded_at is None
