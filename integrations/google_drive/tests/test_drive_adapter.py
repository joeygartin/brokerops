"""GoogleDriveFilesAdapter against the in-process stub — offline, zero credentials."""

import httpx
import pytest

from brokerops_google_drive.adapter import DRIVE_API_BASE, GoogleDriveFilesAdapter
from brokerops_google_drive.stub import create_stub_app


@pytest.fixture
def adapter() -> GoogleDriveFilesAdapter:
    transport = httpx.ASGITransport(app=create_stub_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://drive.test")
    return GoogleDriveFilesAdapter(base_url="http://drive.test", client=client)


async def test_list_seeded_folder(adapter: GoogleDriveFilesAdapter) -> None:
    refs = await adapter.list("RM1004")
    names = [ref.name for ref in refs]
    assert "Purchase agreement.pdf" in names
    assert "Home inspection report.pdf" in names
    first = refs[0]
    assert first.file_id and first.mime_type == "application/pdf"
    assert first.size_bytes and first.size_bytes > 0
    assert first.web_url.endswith(f"/view/{first.file_id}")


async def test_list_unknown_folder_is_empty(adapter: GoogleDriveFilesAdapter) -> None:
    assert await adapter.list("NO-SUCH-FOLDER") == []


async def test_get_roundtrip_and_missing(adapter: GoogleDriveFilesAdapter) -> None:
    listed = await adapter.list("RM1010")
    fetched = await adapter.get(listed[0].file_id)
    assert fetched == listed[0]
    assert await adapter.get("drive-nope") is None


async def test_put_creates_folder_and_file(adapter: GoogleDriveFilesAdapter) -> None:
    ref = await adapter.put("Addendum A.pdf", b"synthetic addendum text", "RM9999")
    assert ref.name == "Addendum A.pdf"
    assert ref.size_bytes == len(b"synthetic addendum text")
    # visible in a subsequent list of the (newly created) folder
    assert [f.name for f in await adapter.list("RM9999")] == ["Addendum A.pdf"]
    # and fetchable by id
    fetched = await adapter.get(ref.file_id)
    assert fetched is not None and fetched.name == "Addendum A.pdf"


async def test_put_into_existing_folder(adapter: GoogleDriveFilesAdapter) -> None:
    before = len(await adapter.list("RM1002"))
    await adapter.put("Wire instructions.pdf", b"synthetic", "RM1002")
    assert len(await adapter.list("RM1002")) == before + 1


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


def test_real_api_without_credentials_fails_loud() -> None:
    with pytest.raises(RuntimeError, match="GOOGLE_DRIVE_CREDENTIALS_FILE"):
        GoogleDriveFilesAdapter(base_url=DRIVE_API_BASE)


def test_missing_credentials_file_fails_loud(tmp_path: object) -> None:
    with pytest.raises(FileNotFoundError):
        GoogleDriveFilesAdapter(credentials_file=f"{tmp_path}/absent.json")
