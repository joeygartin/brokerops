"""MCP server exposing the office-files tools: list_files, get_file, put_file.

Runs standalone over stdio (`uv run mcp-server-google-drive`); points at any
Drive-shaped endpoint via GOOGLE_DRIVE_BASE_URL (real API by default, stub in
demo). Real-API use requires GOOGLE_DRIVE_CREDENTIALS_FILE (service-account
JSON mounted from Secret Manager) — the adapter fails loud without it.
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from brokerops_google_drive.adapter import DRIVE_API_BASE, GoogleDriveFilesAdapter

mcp = FastMCP("google_drive")


def _adapter() -> GoogleDriveFilesAdapter:
    return GoogleDriveFilesAdapter(
        credentials_file=os.environ.get("GOOGLE_DRIVE_CREDENTIALS_FILE") or None,
        base_url=os.environ.get("GOOGLE_DRIVE_BASE_URL", DRIVE_API_BASE),
    )


async def list_files(folder: str) -> list[dict[str, Any]]:
    """List the files inside a named office folder (empty list if absent)."""
    refs = await _adapter().list(folder)
    return [ref.model_dump(mode="json") for ref in refs]


async def get_file(file_id: str) -> dict[str, Any] | None:
    """Fetch one file pointer (name, type, size, open link) by file id."""
    ref = await _adapter().get(file_id)
    return ref.model_dump(mode="json") if ref else None


async def put_file(name: str, content_text: str, folder: str) -> dict[str, Any]:
    """Store text content as a new file inside a named office folder."""
    ref = await _adapter().put(name, content_text.encode("utf-8"), folder)
    return ref.model_dump(mode="json")


mcp.tool()(list_files)
mcp.tool()(get_file)
mcp.tool()(put_file)


def main() -> None:
    mcp.run()
