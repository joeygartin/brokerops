"""FilesPort adapter speaking the Google Drive v3 REST API.

Credentials are a per-client service-account JSON file (mounted from Secret
Manager — never the repo); the adapter mints and refreshes its own bearer via
google-auth. The same adapter runs against the bundled stub (demo mode) through
an injected client or an overridden base URL — swapping is wiring, not code.
Drive payload shapes never leave this module: callers see only `FileRef`.

`folder` is a plain folder *name* (brokerops' convention: one folder per
listing key); the Drive folder-id plumbing stays in here so the port remains
storage-shaped, not Drive-shaped.
"""

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import httpx

from brokerops_core.models.files import FileRef

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials

DRIVE_API_BASE = "https://www.googleapis.com"
FOLDER_MIME = "application/vnd.google-apps.folder"

# The full-drive scope: the adapter both reads office folders and uploads into
# them. A read-only deploy can swap this for drive.readonly at wiring later.
_SCOPES = ["https://www.googleapis.com/auth/drive"]
_FIELDS = "id,name,mimeType,size,webViewLink"


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


class GoogleDriveFilesAdapter:
    def __init__(
        self,
        credentials_file: str | None = None,
        base_url: str = DRIVE_API_BASE,
        client: httpx.AsyncClient | None = None,
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

    async def _headers(self) -> dict[str, str]:
        if self._credentials is None:
            return {}
        if not self._credentials.valid:
            from google.auth.transport import requests as google_requests

            # Token refresh is synchronous network I/O; keep it off the event loop.
            await asyncio.to_thread(self._credentials.refresh, google_requests.Request())
        return {"Authorization": f"Bearer {self._credentials.token}"}

    async def _find_folder_id(self, folder: str) -> str | None:
        query = f"name = '{_escape(folder)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        response = await self._client.get(
            "/drive/v3/files",
            params={"q": query, "fields": "files(id)"},
            headers=await self._headers(),
        )
        response.raise_for_status()
        files = response.json().get("files", [])
        return str(files[0]["id"]) if files else None

    async def _create_folder(self, folder: str) -> str:
        response = await self._client.post(
            "/drive/v3/files",
            json={"name": folder, "mimeType": FOLDER_MIME},
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
        response = await self._client.get(
            "/drive/v3/files",
            params={"q": query, "fields": f"files({_FIELDS})", "orderBy": "name"},
            headers=await self._headers(),
        )
        response.raise_for_status()
        return [file_ref_from_drive(item) for item in response.json().get("files", [])]

    async def get(self, file_id: str) -> FileRef | None:
        response = await self._client.get(
            f"/drive/v3/files/{file_id}",
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
        created = await self._client.post(
            "/drive/v3/files",
            json={"name": name, "parents": [folder_id]},
            params={"fields": "id"},
            headers=await self._headers(),
        )
        created.raise_for_status()
        file_id = str(created.json()["id"])
        uploaded = await self._client.patch(
            f"/upload/drive/v3/files/{file_id}",
            params={"uploadType": "media", "fields": _FIELDS},
            content=content,
            headers={**await self._headers(), "Content-Type": "application/octet-stream"},
        )
        uploaded.raise_for_status()
        return file_ref_from_drive(uploaded.json())
