"""Google Drive stub — a recorded-response double of the Drive v3 endpoints we use.

Demo mode runs with zero credentials. The stub keeps an in-memory file tree
seeded with synthetic transaction folders (one per demo listing key) under a
fixed root folder, so the Documents tab has something to attach on first boot.
It implements exactly the Drive v3 surface the adapter speaks: files.list with
combined name/mimeType/parent `q` constraints, orderBy, and **pagination**
(a deliberately tiny page size, so any consumer that fails to follow
nextPageToken loses rows in tests the way it would against real Drive),
get-by-id, metadata create, and media upload.

Every file also gets a working `webViewLink` pointing at this stub's own
/view/{id} page, so "open" genuinely opens something in the demo.
"""

import html
import os
import re
from itertools import count
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

FOLDER_MIME = "application/vnd.google-apps.folder"

# The seeded root folder every transaction folder lives under. A fixed id so
# demos/tests can anchor the adapter with GOOGLE_DRIVE_ROOT_FOLDER_ID.
ROOT_FOLDER_ID = "drive-root"
ROOT_FOLDER_NAME = "brokerops-transactions"

# Tiny on purpose: the 3-file demo folders already span multiple pages, so the
# adapter's nextPageToken loop is exercised by every listing.
STUB_PAGE_SIZE = 2

# Synthetic office paperwork per demo listing (see api demo_seed.py) — the demo
# attaches these to transactions. All content is fully synthetic.
SEED_TREE: dict[str, list[tuple[str, str]]] = {
    "RM1004": [
        ("Purchase agreement.pdf", "Residential purchase agreement for 1420 Sierra Vista Dr."),
        ("Seller disclosures.pdf", "Transfer disclosure statement signed by the sellers."),
        ("Home inspection report.pdf", "Inspection summary: roof, electrical, and plumbing OK."),
    ],
    "RM1010": [
        ("Purchase agreement.pdf", "Residential purchase agreement for 88 Quartz Hill Rd."),
        ("Buyer preapproval letter.pdf", "Lender preapproval up to the offered amount."),
    ],
    "RM1002": [
        ("Loan approval letter.pdf", "Final underwriting approval for the buyer's loan."),
        ("Closing disclosure.pdf", "Closing disclosure with final settlement figures."),
    ],
}

_NAME_QUERY = re.compile(r"name = '((?:[^'\\]|\\.)*)'")
_PARENT_QUERY = re.compile(r"'((?:[^'\\]|\\.)*)' in parents")


def _unescape(value: str) -> str:
    return value.replace("\\'", "'").replace("\\\\", "\\")


def create_stub_app() -> FastAPI:
    nodes: dict[str, dict[str, Any]] = {}
    ids = count(1)
    seq = count(1)  # creation order, for the createdTime sort
    view_base = os.environ.get("DRIVE_STUB_PUBLIC_URL", "http://localhost:8004").rstrip("/")

    def add_node(
        name: str,
        mime_type: str,
        parent: str | None = None,
        content: bytes = b"",
        node_id: str | None = None,
    ) -> dict[str, Any]:
        node_id = node_id or f"drive-{next(ids):04d}"
        node = {
            "id": node_id,
            "name": name,
            "mimeType": mime_type,
            "parents": [parent] if parent else [],
            "content": content,
            "seq": next(seq),
        }
        nodes[node_id] = node
        return node

    add_node(ROOT_FOLDER_NAME, FOLDER_MIME, node_id=ROOT_FOLDER_ID)
    for folder_name, files in SEED_TREE.items():
        folder = add_node(folder_name, FOLDER_MIME, parent=ROOT_FOLDER_ID)
        for file_name, text in files:
            add_node(file_name, "application/pdf", parent=folder["id"], content=text.encode())

    def payload(node: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": node["id"],
            "name": node["name"],
            "mimeType": node["mimeType"],
            "webViewLink": f"{view_base}/view/{node['id']}",
        }
        if node["mimeType"] != FOLDER_MIME:
            item["size"] = str(len(node["content"]))
        return item

    app = FastAPI(title="Google Drive stub")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/drive/v3/files")
    async def list_files(
        q: str = "",
        orderBy: str = "",  # noqa: N803 - Drive's own parameter casing
        pageSize: int = STUB_PAGE_SIZE,  # noqa: N803
        pageToken: str = "",  # noqa: N803
    ) -> dict[str, Any]:
        # Apply every constraint present in q (the adapter combines name +
        # mimeType + parent), like real Drive does.
        found = list(nodes.values())
        parent_match = _PARENT_QUERY.search(q)
        if parent_match:
            parent_id = _unescape(parent_match.group(1))
            found = [n for n in found if parent_id in n["parents"]]
        name_match = _NAME_QUERY.search(q)
        if name_match:
            name = _unescape(name_match.group(1))
            found = [n for n in found if n["name"] == name]
        if FOLDER_MIME in q:
            found = [n for n in found if n["mimeType"] == FOLDER_MIME]

        if orderBy.startswith("createdTime"):
            found.sort(key=lambda n: int(n["seq"]))
        else:
            found.sort(key=lambda n: str(n["name"]))

        # Paginate like real Drive: a page never exceeds the server's own cap
        # (here deliberately tiny), and more rows mean a nextPageToken.
        offset = int(pageToken) if pageToken else 0
        limit = max(1, min(pageSize, STUB_PAGE_SIZE))
        page = found[offset : offset + limit]
        body: dict[str, Any] = {"files": [payload(n) for n in page]}
        if offset + limit < len(found):
            body["nextPageToken"] = str(offset + limit)
        return body

    @app.get("/drive/v3/files/{file_id}")
    async def get_file(file_id: str) -> dict[str, Any]:
        node = nodes.get(file_id)
        if node is None:
            raise HTTPException(status_code=404, detail="file not found")
        return payload(node)

    @app.post("/drive/v3/files", status_code=200)
    async def create_file(body: dict[str, Any]) -> dict[str, Any]:
        parents = body.get("parents") or []
        node = add_node(
            name=str(body.get("name", "")),
            mime_type=str(body.get("mimeType", "application/octet-stream")),
            parent=str(parents[0]) if parents else None,
        )
        return payload(node)

    @app.patch("/upload/drive/v3/files/{file_id}")
    async def upload_media(file_id: str, request: Request) -> dict[str, Any]:
        node = nodes.get(file_id)
        if node is None:
            raise HTTPException(status_code=404, detail="file not found")
        node["content"] = await request.body()
        return payload(node)

    @app.get("/view/{file_id}", response_class=HTMLResponse)
    async def view_file(file_id: str) -> str:
        node = nodes.get(file_id)
        if node is None:
            raise HTTPException(status_code=404, detail="file not found")
        # Names and content are operator-supplied — escape both so a crafted
        # upload can't turn this viewer into stored XSS.
        name = html.escape(str(node["name"]))
        text = html.escape(node["content"].decode("utf-8", errors="replace"))
        return (
            "<!doctype html><html><head><title>"
            f'{name}</title></head><body style="font-family: system-ui; '
            'padding: 2rem">'
            f"<h1>{name}</h1><p>{text or '(folder)'}"
            "</p><p><em>Synthetic demo file served by the bundled Drive stub.</em></p>"
            "</body></html>"
        )

    return app


app = create_stub_app()
