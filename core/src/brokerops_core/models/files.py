"""Storage-neutral file pointer (BOP-021).

brokerops never stores file bytes — only pointers into the office file store.
`FileRef` is deliberately storage-shaped, not Drive-shaped: nothing here names
Google, so a Dropbox / OneDrive / SharePoint adapter returns the same model.
"""

from pydantic import BaseModel


class FileRef(BaseModel):
    """A pointer to a file living in the office file store.

    `file_id` is the backend's opaque identifier; `web_url` is where a human
    opens the file (the provider's own viewer). No provider-specific fields.
    """

    file_id: str
    name: str
    mime_type: str = ""
    size_bytes: int | None = None
    web_url: str = ""
