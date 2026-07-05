"""FilesPort adapter speaking the Google Drive v3 REST API.

Credentials are a per-client service-account JSON file (mounted from Secret
Manager — never the repo); the adapter mints and refreshes its own bearer via
google-auth. The same adapter runs against the bundled stub (demo mode) through
an injected client or an overridden base URL — swapping is wiring, not code.
Drive payload shapes never leave this module: callers see only `FileRef`.

`folder` is a plain folder *name* (brokerops' convention: one folder per
listing key); the Drive folder-id plumbing stays in here so the port remains
storage-shaped, not Drive-shaped. When GOOGLE_DRIVE_ROOT_FOLDER_ID is
configured, folder lookup and creation are anchored under that root, so a
same-named folder elsewhere in the service account's corpus can't hijack a
transaction's paperwork.
"""

import asyncio
import logging
import mimetypes
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from brokerops_core.models.files import FileRef

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com"
FOLDER_MIME = "application/vnd.google-apps.folder"

# The full-drive scope: the adapter both reads office folders and uploads into
# them. A read-only deploy can swap this for drive.readonly at wiring later.
_SCOPES = ["https://www.googleapis.com/auth/drive"]
_FIELDS = "id,name,mimeType,size,webViewLink"
# Drive caps pageSize at 1000; ask for the max so most folders are one page.
_PAGE_SIZE = 1000


def file_ref_from_drive(payload: Mapping[str, Any]) -> FileRef:
    size = payload.get("size")
    return FileRef(
        file_id=str(payload["id"]),
        name=str(payload.get("name", "")),
        mime_type=str(payload.get("mimeType", "")),
        size_bytes=int(size) if size is not None else None,
        web_url=str(payload.get("webViewLink", "")),
    )


def _escape(value: str) -> str:
    """Escape a value for a single-quoted Drive query-string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _file_path(file_id: str) -> str:
    """A file id is one URL path segment — percent-encode everything so an
    attacker-influenced id ("abc/permissions", "x?alt=media") cannot reroute
    the request the service-account token authorizes."""
    return quote(file_id, safe="")


class GoogleDriveFilesAdapter:
    def __init__(
        self,
        credentials_file: str | None = None,
        base_url: str = DRIVE_API_BASE,
        client: httpx.AsyncClient | None = None,
        root_folder_id: str | None = None,
    ) -> None:
        # Fail loud (ADR-0014 posture): the real Drive API without credentials is
        # a deploy misconfiguration, not a reason to limp along unauthenticated.
        # A non-default base URL (the bundled stub) or an injected client needs none.
        self._credentials: Credentials | None = None
        if credentials_file:
            from google.oauth2.service_account import Credentials as ServiceAccountCredentials

            # google-auth's service_account constructor lacks annotations upstream.
            self._credentials = ServiceAccountCredentials.from_service_account_file(  # type: ignore[no-untyped-call]
                filename=credentials_file,
                scopes=_SCOPES,
            )
        elif client is None and base_url == DRIVE_API_BASE:
            raise RuntimeError(
                "GoogleDriveFilesAdapter requires GOOGLE_DRIVE_CREDENTIALS_FILE (a "
                "service-account JSON mounted from Secret Manager) to reach the real "
                "Google Drive API"
            )
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=30.0)
        self._root_folder_id = root_folder_id
        # Serialize token refresh: concurrent requests must not each spawn a
        # refresh thread against the same mutable credentials object.
        self._refresh_lock = asyncio.Lock()

    async def _headers(self) -> dict[str, str]:
        if self._credentials is None:
            return {}
        if not self._credentials.valid:
            async with self._refresh_lock:
                if not self._credentials.valid:  # re-check under the lock
                    from google.auth.transport import requests as google_requests

                    # Token refresh is synchronous network I/O; keep it off the event loop.
                    await asyncio.to_thread(self._credentials.refresh, google_requests.Request())
        return {"Authorization": f"Bearer {self._credentials.token}"}

    async def _search(self, query: str, *, fields: str, order_by: str) -> list[dict[str, Any]]:
        """Run a files.list query to exhaustion. Real Drive silently caps a
        single page (default 100), so a folder past that size would otherwise
        lose documents with no error — always follow nextPageToken."""
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "q": query,
                "fields": f"nextPageToken,files({fields})",
                "orderBy": order_by,
                "pageSize": _PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            response = await self._client.get(
                "/drive/v3/files", params=params, headers=await self._headers()
            )
            response.raise_for_status()
            payload = response.json()
            results.extend(payload.get("files", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return results

    async def _find_folder_id(self, folder: str) -> str | None:
        query = f"name = '{_escape(folder)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        if self._root_folder_id:
            query += f" and '{_escape(self._root_folder_id)}' in parents"
        # Deterministic resolution: Drive allows duplicate folder names, so
        # order by creation time and always pick the oldest — a later duplicate
        # (or a same-named folder elsewhere, when unanchored) can't silently
        # start splitting a transaction's files.
        matches = await self._search(query, fields="id", order_by="createdTime")
        if not matches:
            return None
        if len(matches) > 1:
            anchor = (
                f"under root {self._root_folder_id!r}" if self._root_folder_id else "drive-wide"
            )
            logger.warning(
                "google_drive: %d folders named %r (%s); using the oldest (%s) — "
                "merge or rename the duplicates",
                len(matches),
                folder,
                anchor,
                matches[0]["id"],
            )
        return str(matches[0]["id"])

    async def _create_folder(self, folder: str) -> str:
        metadata: dict[str, Any] = {"name": folder, "mimeType": FOLDER_MIME}
        if self._root_folder_id:
            metadata["parents"] = [self._root_folder_id]
        response = await self._client.post(
            "/drive/v3/files",
            json=metadata,
            params={"fields": "id"},
            headers=await self._headers(),
        )
        response.raise_for_status()
        return str(response.json()["id"])

    async def list(self, folder: str) -> list[FileRef]:
        folder_id = await self._find_folder_id(folder)
        if folder_id is None:
            return []
        query = f"'{_escape(folder_id)}' in parents and trashed = false"
        items = await self._search(query, fields=_FIELDS, order_by="name")
        return [file_ref_from_drive(item) for item in items]

    async def get(self, file_id: str) -> FileRef | None:
        response = await self._client.get(
            f"/drive/v3/files/{_file_path(file_id)}",
            params={"fields": _FIELDS},
            headers=await self._headers(),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return file_ref_from_drive(response.json())

    async def put(self, name: str, content: bytes, folder: str) -> FileRef:
        folder_id = await self._find_folder_id(folder)
        if folder_id is None:
            folder_id = await self._create_folder(folder)
        # Two-step upload: create the metadata row, then send the bytes as a
        # media update — both plain Drive v3 endpoints, trivially stub-able.
        # Name-derived content type on both steps, so a real-Drive upload does
        # not land as application/octet-stream.
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        created = await self._client.post(
            "/drive/v3/files",
            json={"name": name, "parents": [folder_id], "mimeType": mime_type},
            params={"fields": "id"},
            headers=await self._headers(),
        )
        created.raise_for_status()
        file_id = str(created.json()["id"])
        uploaded = await self._client.patch(
            f"/upload/drive/v3/files/{_file_path(file_id)}",
            params={"uploadType": "media", "fields": _FIELDS},
            content=content,
            headers={**await self._headers(), "Content-Type": mime_type},
        )
        uploaded.raise_for_status()
        return file_ref_from_drive(uploaded.json())
