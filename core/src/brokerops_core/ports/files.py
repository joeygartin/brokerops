from typing import Protocol

from brokerops_core.models.files import FileRef


class FilesPort(Protocol):
    """Boundary to the office file store (Google Drive in V1).

    Deliberately storage-shaped, not Drive-shaped: `folder` is a plain folder
    name and `file_id` an opaque backend identifier, so Dropbox / OneDrive /
    SharePoint are future adapters, not redesigns. brokerops itself stores only
    `FileRef` pointers — file bytes never enter the domain.
    """

    async def list(self, folder: str) -> list[FileRef]:
        """Files inside `folder`. An unknown folder is empty, not an error."""
        ...

    async def get(self, file_id: str) -> FileRef | None: ...

    async def put(self, name: str, content: bytes, folder: str) -> FileRef:
        """Store `content` as `name` inside `folder` (created if missing)."""
        ...
