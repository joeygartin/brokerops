"""The BOP-021 documents flow through the real wiring, zero credentials.

`with TestClient(app)` runs the lifespan: in-memory stores behind the scoped
wrappers, the in-process Drive stub behind the recording + idempotency seams,
demo tenant bound by the middleware. Proves the acceptance criteria: browse the
stub tree, attach → visible on the transaction, milestone expected-document
status flips, replay is idempotent, uploads land in the action ledger, and the
DB rows carry pointers only.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from brokerops_api.main import app
from brokerops_core.models.document import Document, DocumentKind
from brokerops_core.models.files import FileRef
from brokerops_core.services.idempotency import IdempotentFiles
from brokerops_core.services.scoped_stores import ScopedDocumentStore
from brokerops_core.services.tenancy import CrossTenantError, tenant_scope

TXN = "TXN-1001"  # demo transaction on listing RM1004 (see demo_seed.py)


def _seed(client: TestClient) -> None:
    assert client.post("/demo/seed", json={"reset": True}).status_code == 200


def _inspection_report_id(client: TestClient) -> str:
    files = client.get("/files", params={"folder": "RM1004"}).json()
    return str(next(f["file_id"] for f in files if f["name"] == "Home inspection report.pdf"))


def test_lifespan_wires_scoped_documents_and_seamed_files() -> None:
    with TestClient(app):
        assert isinstance(app.state.document_store, ScopedDocumentStore)
        assert isinstance(app.state.files, IdempotentFiles)


def test_browse_attach_open_and_milestone_status() -> None:
    with TestClient(app) as client:
        _seed(client)

        # Browse: the seeded stub tree for this transaction's listing folder.
        files = client.get("/files", params={"folder": "RM1004"}).json()
        assert {f["name"] for f in files} >= {
            "Purchase agreement.pdf",
            "Home inspection report.pdf",
        }
        # unknown folder is empty, not an error
        assert client.get("/files", params={"folder": "NOPE"}).json() == []

        # Before any attach: the inspection milestone expects a document, unmet.
        detail = client.get(f"/transactions/{TXN}").json()
        inspection = next(m for m in detail["milestones"] if m["id"] == "MS-1001-INS")
        assert inspection["expected_document"] == "inspection_report"
        assert inspection["document_satisfied"] is False
        assert detail["documents"] == []

        # Attach the inspection report.
        file_id = _inspection_report_id(client)
        attached = client.post(
            f"/transactions/{TXN}/documents",
            json={"file_id": file_id, "kind": "inspection_report"},
        )
        assert attached.status_code == 201
        document = attached.json()
        assert document["file"]["file_id"] == file_id
        assert document["file"]["web_url"]  # open link present
        assert document["uploaded_by"]  # attributed to the operator
        assert "content" not in document["file"]  # pointer only, no bytes

        # Visible on the transaction, and the milestone flips to satisfied.
        detail = client.get(f"/transactions/{TXN}").json()
        assert [d["id"] for d in detail["documents"]] == [document["id"]]
        inspection = next(m for m in detail["milestones"] if m["id"] == "MS-1001-INS")
        assert inspection["document_satisfied"] is True
        # the other transaction's inspection milestone stays unmet
        other = client.get("/transactions/TXN-1002").json()
        other_inspection = next(m for m in other["milestones"] if m["id"] == "MS-1002-INS")
        assert other_inspection["document_satisfied"] is False

        # Repeat attach = idempotent replay (200), no duplicate row.
        replay = client.post(
            f"/transactions/{TXN}/documents",
            json={"file_id": file_id, "kind": "inspection_report"},
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == document["id"]
        assert len(client.get(f"/transactions/{TXN}/documents").json()) == 1


def test_attach_validations() -> None:
    with TestClient(app) as client:
        _seed(client)
        file_id = _inspection_report_id(client)
        # unknown transaction / unknown file → 404
        assert (
            client.post("/transactions/NOPE/documents", json={"file_id": file_id}).status_code
            == 404
        )
        assert (
            client.post(
                f"/transactions/{TXN}/documents", json={"file_id": "drive-nope"}
            ).status_code
            == 404
        )
        # a milestone belonging to another transaction → 422
        response = client.post(
            f"/transactions/{TXN}/documents",
            json={"file_id": file_id, "milestone_id": "MS-1002-INS"},
        )
        assert response.status_code == 422
        # a milestone of this transaction is accepted
        response = client.post(
            f"/transactions/{TXN}/documents",
            json={"file_id": file_id, "milestone_id": "MS-1001-INS"},
        )
        assert response.status_code == 201


def test_upload_goes_through_the_write_seam_into_the_ledger() -> None:
    with TestClient(app) as client:
        _seed(client)
        response = client.post(
            f"/transactions/{TXN}/documents/upload",
            json={
                "name": "Repair addendum.pdf",
                "content_text": "synthetic addendum",
                "kind": "other",
            },
        )
        assert response.status_code == 201
        document = response.json()
        assert document["title"] == "Repair addendum.pdf"

        # The new file landed in the store (default folder = the listing key)…
        names = {f["name"] for f in client.get("/files", params={"folder": "RM1004"}).json()}
        assert "Repair addendum.pdf" in names

        # …and the external write is in the action audit-ledger with a digest,
        # never the content.
        ledger = client.get("/audit").json()
        put_records = [r for r in ledger if r["tool"] == "put_file"]
        assert len(put_records) == 1
        record = put_records[0]
        assert record["integration"] == "google_drive"
        assert record["outcome"] == "success"
        assert record["external_id"] == document["file"]["file_id"]
        assert record["args"]["name"] == "Repair addendum.pdf"
        assert "content_text" not in record["args"] and "content" not in record["args"]


def test_cross_tenant_document_write_through_wired_store_is_audited() -> None:
    # The BOP-006/ADR-0012 proof for documents: a write claiming another tenant
    # through the store the lifespan actually wires is denied and lands a
    # security event in the same audit ledger the operator can query.
    with TestClient(app):
        store = app.state.document_store
        audit = app.state.audit_log

        async def attempt() -> None:
            with tenant_scope("demo"):
                foreign = Document(
                    tenant_id="other-brokerage",
                    id="doc-evil",
                    transaction_id="tx1",
                    kind=DocumentKind.OTHER,
                    title="hijack",
                    file=FileRef(file_id="f1", name="hijack.pdf"),
                    created_at=datetime.now(UTC),
                )
                try:
                    await store.add(foreign)
                except CrossTenantError:
                    pass

        asyncio.run(attempt())
        records = asyncio.run(audit.list())
    security = [r for r in records if r.integration == "security"]
    assert len(security) == 1
    assert security[0].tool == "add_document"
    assert security[0].args == {"attempted_tenant": "other-brokerage", "bound_tenant": "demo"}


def test_files_provider_selector_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    from brokerops_api.deps import build_files_adapter

    # unknown value errors at wiring
    monkeypatch.setenv("FILES_PROVIDER", "dropbox")
    with pytest.raises(RuntimeError, match="unknown FILES_PROVIDER"):
        build_files_adapter()
    # google_drive without credentials errors at wiring — never a silent stub downgrade
    monkeypatch.setenv("FILES_PROVIDER", "google_drive")
    monkeypatch.delenv("GOOGLE_DRIVE_CREDENTIALS_FILE", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_DRIVE_CREDENTIALS_FILE"):
        build_files_adapter()
    # the Terraform never-pushed placeholder counts as unset
    monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "unset")
    with pytest.raises(RuntimeError, match="GOOGLE_DRIVE_CREDENTIALS_FILE"):
        build_files_adapter()
    # unset/stub → the bundled in-process stub, zero credentials
    monkeypatch.delenv("FILES_PROVIDER", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_BASE_URL", raising=False)
    adapter = build_files_adapter()
    assert type(adapter).__name__ == "GoogleDriveFilesAdapter"
