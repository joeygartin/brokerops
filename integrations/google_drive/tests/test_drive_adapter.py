"""GoogleDriveFilesAdapter against the in-process stub — offline, zero credentials.

The stub paginates with a deliberately tiny page (STUB_PAGE_SIZE=2), so every
listing here also proves the adapter's nextPageToken loop — an adapter that
read only the first page would fail these the same way it would silently lose
documents against real Drive.
"""

import httpx
import pytest

from brokerops_google_drive.adapter import DRIVE_API_BASE, FOLDER_MIME, GoogleDriveFilesAdapter
from brokerops_google_drive.stub import ROOT_FOLDER_ID, STUB_PAGE_SIZE, create_stub_app


def _make(
    app: object | None = None, root_folder_id: str | None = None
) -> tuple[GoogleDriveFilesAdapter, httpx.AsyncClient]:
    stub = app or create_stub_app()
    transport = httpx.ASGITransport(app=stub)  # type: ignore[arg-type]
    client = httpx.AsyncClient(transport=transport, base_url="http://drive.test")
    adapter = GoogleDriveFilesAdapter(
        base_url="http://drive.test", client=client, root_folder_id=root_folder_id
    )
    return adapter, client


@pytest.fixture
def adapter() -> GoogleDriveFilesAdapter:
    return _make()[0]


async def test_list_seeded_folder(adapter: GoogleDriveFilesAdapter) -> None:
    refs = await adapter.list("RM1004")
    names = [ref.name for ref in refs]
    assert "Purchase agreement.pdf" in names
    assert "Home inspection report.pdf" in names
    first = refs[0]
    assert first.file_id and first.mime_type == "application/pdf"
    assert first.size_bytes and first.size_bytes > 0
    assert first.web_url.endswith(f"/view/{first.file_id}")


async def test_list_follows_pagination_to_exhaustion(adapter: GoogleDriveFilesAdapter) -> None:
    # Well past the stub's page size: only a nextPageToken loop sees them all.
    total = STUB_PAGE_SIZE * 3 + 1
    for index in range(total):
        await adapter.put(f"Addendum {index:02d}.pdf", b"synthetic", "RM8888")
    refs = await adapter.list("RM8888")
    assert len(refs) == total
    assert len({ref.file_id for ref in refs}) == total  # distinct rows, no page repeated


async def test_list_unknown_folder_is_empty(adapter: GoogleDriveFilesAdapter) -> None:
    assert await adapter.list("NO-SUCH-FOLDER") == []


async def test_get_roundtrip_and_missing(adapter: GoogleDriveFilesAdapter) -> None:
    listed = await adapter.list("RM1010")
    fetched = await adapter.get(listed[0].file_id)
    assert fetched == listed[0]
    assert await adapter.get("drive-nope") is None


async def test_get_percent_encodes_the_file_id(adapter: GoogleDriveFilesAdapter) -> None:
    # An attacker-influenced id must stay ONE path segment: it can't grow the
    # request into another Drive endpoint ("abc/permissions") or smuggle query
    # parameters ("x?alt=media") under the service account's token.
    assert await adapter.get("abc/permissions") is None
    assert await adapter.get("x?alt=media") is None
    assert await adapter.get("../files") is None


async def test_put_creates_folder_and_file(adapter: GoogleDriveFilesAdapter) -> None:
    ref = await adapter.put("Addendum A.pdf", b"synthetic addendum text", "RM9999")
    assert ref.name == "Addendum A.pdf"
    assert ref.size_bytes == len(b"synthetic addendum text")
    # name-derived content type, not octet-stream (real Drive keeps this)
    assert ref.mime_type == "application/pdf"
    # visible in a subsequent list of the (newly created) folder
    assert [f.name for f in await adapter.list("RM9999")] == ["Addendum A.pdf"]
    # and fetchable by id
    fetched = await adapter.get(ref.file_id)
    assert fetched is not None and fetched.name == "Addendum A.pdf"


async def test_put_into_existing_folder(adapter: GoogleDriveFilesAdapter) -> None:
    before = len(await adapter.list("RM1002"))
    await adapter.put("Wire instructions.pdf", b"synthetic", "RM1002")
    assert len(await adapter.list("RM1002")) == before + 1


async def test_duplicate_folder_names_resolve_to_the_oldest_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_stub_app()
    adapter, client = _make(app)
    # A second folder named RM1004 appears elsewhere in the corpus.
    await client.post("/drive/v3/files", json={"name": "RM1004", "mimeType": FOLDER_MIME})
    with caplog.at_level("WARNING"):
        refs = await adapter.list("RM1004")
    # Deterministic: the ORIGINAL (oldest) folder's files, not an arbitrary pick
    # or the empty impostor.
    assert {r.name for r in refs} >= {"Purchase agreement.pdf", "Home inspection report.pdf"}
    assert any("folders named 'RM1004'" in message for message in caplog.messages)
    # put lands in the same (oldest) folder instead of racing into the duplicate
    await adapter.put("Counter offer.pdf", b"synthetic", "RM1004")
    assert "Counter offer.pdf" in {r.name for r in await adapter.list("RM1004")}


async def test_root_anchoring_ignores_same_named_folders_outside_the_root() -> None:
    app = create_stub_app()
    anchored, client = _make(app, root_folder_id=ROOT_FOLDER_ID)
    unanchored, _ = _make(app)
    # A same-named folder OUTSIDE the configured root, holding a decoy file.
    stray = await client.post("/drive/v3/files", json={"name": "RM7777", "mimeType": FOLDER_MIME})
    stray_id = stray.json()["id"]
    decoy = await client.post("/drive/v3/files", json={"name": "Decoy.pdf", "parents": [stray_id]})
    await client.patch(
        f"/upload/drive/v3/files/{decoy.json()['id']}",
        params={"uploadType": "media"},
        content=b"decoy",
    )
    assert {r.name for r in await unanchored.list("RM7777")} == {"Decoy.pdf"}
    # Anchored: the stray folder does not exist under the root…
    assert await anchored.list("RM7777") == []
    # …and put creates the folder UNDER the root rather than adopting the stray.
    await anchored.put("Real doc.pdf", b"synthetic", "RM7777")
    assert {r.name for r in await anchored.list("RM7777")} == {"Real doc.pdf"}
    # seeded folders live under the root, so anchored lookup still finds them
    assert len(await anchored.list("RM1004")) == 3


async def test_stub_view_page_serves_the_file(adapter: GoogleDriveFilesAdapter) -> None:
    app = create_stub_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://drive.test") as client:
        listing = await client.get("/drive/v3/files", params={"q": "name = 'RM1004'"})
        folder_id = listing.json()["files"][0]["id"]
        children = await client.get("/drive/v3/files", params={"q": f"'{folder_id}' in parents"})
        file_id = children.json()["files"][0]["id"]
        page = await client.get(f"/view/{file_id}")
        assert page.status_code == 200
        assert "Synthetic demo file" in page.text


async def test_stub_view_page_escapes_stored_markup() -> None:
    # Operator-supplied name/content must never execute in the viewer.
    app = create_stub_app()
    adapter, client = _make(app)
    ref = await adapter.put(
        '<script>alert("x")</script>.txt', b'<img src=x onerror=alert("y")>', "RM1004"
    )
    page = await client.get(f"/view/{ref.file_id}")
    assert page.status_code == 200
    assert "<script>" not in page.text and "<img" not in page.text
    assert "&lt;script&gt;" in page.text and "&lt;img" in page.text


def test_real_api_without_credentials_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="GOOGLE_DRIVE_CREDENTIALS_FILE"):
        GoogleDriveFilesAdapter(base_url=DRIVE_API_BASE)


def test_missing_credentials_file_fails_loud(tmp_path: object) -> None:
    with pytest.raises(FileNotFoundError):
        GoogleDriveFilesAdapter(credentials_file=f"{tmp_path}/absent.json")
